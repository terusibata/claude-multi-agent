"""
コンテナ内ビルトイン MCP サーバーファクトリ

ビルトイン MCP サーバー（file-tools, file-presentation）と
OpenAPI MCP サーバーを作成する。
"""
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ワークスペースパス
WORKSPACE_DIR = "/workspace"


# =============================================================================
# file-presentation MCP サーバー
# =============================================================================


def _create_file_presentation_server():
    """file-presentation MCP サーバーを作成"""
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping file-presentation MCP server")
        return None

    @tool(
        "present_files",
        (
            "AIが作成・編集したファイルをユーザーに提示する。"
            "ファイルパスのリストと説明を受け取り、ユーザーに提示する情報を返す。"
            "Write/Edit/NotebookEditでファイルを作成・編集した後は、"
            "必ずこのツールを使用してユーザーにファイルを提示してください。"
        ),
        {"file_paths": list[str], "description": str},
    )
    async def present_files_tool(args: dict[str, Any]) -> dict[str, Any]:
        file_paths_input = args.get("file_paths", [])
        description = args.get("description", "")

        # file_paths の正規化
        if isinstance(file_paths_input, str):
            if file_paths_input.startswith("["):
                try:
                    parsed = json.loads(file_paths_input)
                    if isinstance(parsed, list):
                        file_paths_input = parsed
                except json.JSONDecodeError:
                    pass
            if isinstance(file_paths_input, str):
                file_paths_input = [file_paths_input]

        existing_files = []
        missing_files = []

        for file_path in file_paths_input:
            if os.path.isabs(file_path):
                full_path = Path(file_path)
            else:
                full_path = Path(WORKSPACE_DIR) / file_path

            if full_path.exists() and full_path.is_file():
                # 相対パス計算
                try:
                    relative_path = str(full_path.relative_to(WORKSPACE_DIR))
                except ValueError:
                    relative_path = full_path.name

                existing_files.append({
                    "path": str(full_path),
                    "relative_path": relative_path,
                    "name": full_path.name,
                    "size": full_path.stat().st_size,
                    "mime_type": mimetypes.guess_type(str(full_path))[0] or "application/octet-stream",
                    "exists": True,
                })
            else:
                missing_files.append({
                    "relative_path": file_path,
                    "exists": False,
                })

        # 結果メッセージ構築
        parts = []
        if existing_files:
            parts.append(f"ファイルを提示しました: {description}\n")
            parts.append("【提示されたファイル】")
            for f in existing_files:
                parts.append(f"• {f['name']} ({f['size']} bytes)")
                parts.append(f"  ダウンロードパス: {f['relative_path']}")
        else:
            parts.append(f"提示するファイルが見つかりませんでした: {description}")

        if missing_files:
            parts.append("")
            parts.append("【見つからなかったファイル】")
            for f in missing_files:
                parts.append(f"• {f['relative_path']}")

        result_text = "\n".join(parts)

        return {
            "content": [{"type": "text", "text": result_text}],
            "_metadata": {
                "files": existing_files + missing_files,
                "presented_files": existing_files,
                "description": description,
            },
        }

    server = create_sdk_mcp_server(
        name="file-presentation",
        version="1.0.0",
        tools=[present_files_tool],
    )

    return server


# =============================================================================
# file-tools MCP サーバー
# =============================================================================


def _create_file_tools_server():
    """file-tools MCP サーバーを作成"""
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping file-tools MCP server")
        return None

    try:
        from workspace_agent.file_tools.registry import create_file_tools_handlers
    except ImportError:
        logger.warning("workspace_agent.file_tools not available, skipping file-tools MCP server")
        return None

    handlers = create_file_tools_handlers()

    # ツール定義
    tool_schemas = {
        "list_workspace_files": {
            "description": "ワークスペース内のファイル一覧を取得する",
            "schema": {"filter_type": str},
        },
        "read_image_file": {
            "description": "画像ファイルを視覚的に読み込む（base64エンコード）",
            "schema": {"file_path": str, "max_dimension": int},
        },
        "get_sheet_info": {
            "description": "Excelファイルのシート一覧と基本情報を取得する",
            "schema": {"file_path": str},
        },
        "get_sheet_csv": {
            "description": "Excelシートの内容をCSV形式で取得する（行範囲指定可能）",
            "schema": {"file_path": str, "sheet_name": str, "start_row": int, "end_row": int},
        },
        "search_workbook": {
            "description": "Excelワークブック全体からキーワード検索する",
            "schema": {"file_path": str, "query": str, "case_sensitive": bool},
        },
        "inspect_pdf_file": {
            "description": "PDFファイルの基本情報（ページ数、メタデータ等）を取得する",
            "schema": {"file_path": str},
        },
        "read_pdf_pages": {
            "description": "PDFの指定ページのテキストを読み取る",
            "schema": {"file_path": str, "start_page": int, "end_page": int},
        },
        "convert_pdf_to_images": {
            "description": "PDFページを画像に変換する（図表確認用）",
            "schema": {"file_path": str, "page_numbers": str, "dpi": int},
        },
        "get_document_info": {
            "description": "Word文書の基本情報（段落数、セクション等）を取得する",
            "schema": {"file_path": str},
        },
        "get_document_content": {
            "description": "Word文書のテキスト内容を取得する",
            "schema": {"file_path": str, "start_paragraph": int, "end_paragraph": int},
        },
        "search_document": {
            "description": "Word文書内をキーワード検索する",
            "schema": {"file_path": str, "query": str, "case_sensitive": bool},
        },
        "get_presentation_info": {
            "description": "PowerPointプレゼンテーションの基本情報を取得する",
            "schema": {"file_path": str},
        },
        "get_slides_content": {
            "description": "PowerPointスライドのテキスト内容を取得する",
            "schema": {"file_path": str, "start_slide": int, "end_slide": int},
        },
        "search_presentation": {
            "description": "PowerPointプレゼンテーション内をキーワード検索する",
            "schema": {"file_path": str, "query": str, "case_sensitive": bool},
        },
        "inspect_image_file": {
            "description": "画像ファイルの基本情報（サイズ、フォーマット等）を取得する",
            "schema": {"file_path": str},
        },
    }

    tools = []
    for handler_name, handler_func in handlers.items():
        if handler_name not in tool_schemas:
            logger.warning("Unknown handler: %s, skipping", handler_name)
            continue

        schema_info = tool_schemas[handler_name]

        def make_tool(name, desc, schema, func):
            @tool(name, desc, schema)
            async def tool_func(args: dict[str, Any]) -> dict[str, Any]:
                return await func(args)
            return tool_func

        tools.append(make_tool(
            handler_name,
            schema_info["description"],
            schema_info["schema"],
            handler_func,
        ))

    if not tools:
        logger.warning("No file tools created")
        return None

    server = create_sdk_mcp_server(
        name="file-tools",
        version="1.0.0",
        tools=tools,
    )

    return server


# =============================================================================
# パブリック API
# =============================================================================


def create_builtin_mcp_servers() -> dict[str, Any]:
    """コンテナ内ビルトイン MCP サーバーを作成"""
    servers = {}

    file_tools_server = _create_file_tools_server()
    if file_tools_server:
        servers["file-tools"] = file_tools_server

    presentation_server = _create_file_presentation_server()
    if presentation_server:
        servers["file-presentation"] = presentation_server

    return servers


def create_openapi_mcp_servers(configs: list[dict]) -> dict[str, Any]:
    """シリアライズされた設定から OpenAPI MCP サーバーを作成"""
    servers = {}
    for config in configs:
        try:
            from workspace_agent.openapi_mcp import create_openapi_mcp_server

            result = create_openapi_mcp_server(
                openapi_spec=config["openapi_spec"],
                server_name=config["server_name"],
                base_url=config.get("base_url"),
                headers=config.get("headers"),
            )
            if result:
                server, _ = result
                servers[config["server_name"]] = server
        except Exception as e:
            logger.error(
                "OpenAPI MCP server creation failed: %s (server=%s)",
                str(e), config.get("server_name"),
            )
    return servers
