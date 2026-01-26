"""
ビルトインMCPツールの実装
アプリケーション組み込みのMCPサーバーとツール
"""
import os
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


# ビルトインツールの定義
BUILTIN_TOOL_DEFINITIONS = {
    "present_files": {
        "name": "present_files",
        "description": "AIが作成・編集したファイルをユーザーに提示する。ファイルパスのリストと説明を受け取り、ユーザーに提示する情報を返す。Write/Edit/NotebookEditでファイルを作成・編集した後は、必ずこのツールを使用してユーザーにファイルを提示してください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "提示するファイルパスのリスト（作成・編集したファイルのパス）"
                },
                "description": {
                    "type": "string",
                    "description": "ファイルの説明（何を作成・編集したか）"
                }
            },
            "required": ["file_paths", "description"]
        }
    }
}


def create_present_files_handler(
    workspace_cwd: str = "",
    workspace_service=None,
    tenant_id: str = "",
    conversation_id: str = "",
):
    """
    present_filesツールのハンドラーを作成

    Args:
        workspace_cwd: ワークスペースのカレントディレクトリ
        workspace_service: WorkspaceServiceインスタンス（即時S3アップロード用）
        tenant_id: テナントID
        conversation_id: 会話ID

    Returns:
        ツールハンドラー関数
    """
    async def present_files_handler(args: dict[str, Any]) -> dict[str, Any]:
        """
        present_filesツールの実行ハンドラー

        Args:
            args: ツール引数
                - file_paths: ファイルパスのリスト（または単一の文字列パス）
                - description: ファイルの説明

        Returns:
            ツール実行結果
        """
        file_paths_input = args.get("file_paths", [])
        description = args.get("description", "")

        # file_pathsの正規化
        if isinstance(file_paths_input, str):
            # JSON文字列の場合はパース
            if file_paths_input.startswith("["):
                import json
                try:
                    file_paths = json.loads(file_paths_input)
                except json.JSONDecodeError:
                    file_paths = [file_paths_input]
            else:
                file_paths = [file_paths_input]
            logger.info("ファイル提示: file_paths正規化", original=file_paths_input, result=file_paths)
        else:
            file_paths = file_paths_input

        files_info = []
        for file_path in file_paths:
            # 相対パスの場合はworkspace_cwdを基準に解決
            if not os.path.isabs(file_path) and workspace_cwd:
                full_path = os.path.join(workspace_cwd, file_path)
            else:
                full_path = file_path

            path = Path(full_path)
            if path.exists() and path.is_file():
                # MIMEタイプを推測
                mime_type, _ = mimetypes.guess_type(str(path))

                # ファイルがワークスペース外にある場合、ワークスペース内にコピー
                relative_path = file_path
                if workspace_cwd and not str(path.absolute()).startswith(str(Path(workspace_cwd).absolute())):
                    # ワークスペース外のファイル → ワークスペース内にコピー
                    dest_path = Path(workspace_cwd) / path.name

                    # 同名ファイルが存在する場合はユニークな名前を生成
                    counter = 1
                    original_name = dest_path.stem
                    suffix = dest_path.suffix
                    while dest_path.exists():
                        dest_path = Path(workspace_cwd) / f"{original_name}_{counter}{suffix}"
                        counter += 1

                    try:
                        shutil.copy2(str(path), str(dest_path))
                        relative_path = dest_path.name
                        logger.info(
                            "ファイル提示: ワークスペース外ファイルをコピー",
                            source=str(path),
                            destination=str(dest_path)
                        )
                        # コピー後のパスを更新
                        path = dest_path
                    except Exception as copy_error:
                        logger.error(
                            "ファイル提示: ファイルコピー失敗",
                            source=str(path),
                            error=str(copy_error)
                        )
                        files_info.append({
                            "path": full_path,
                            "relative_path": file_path,
                            "name": path.name,
                            "exists": False,
                            "error": f"コピー失敗: {str(copy_error)}"
                        })
                        continue
                elif workspace_cwd:
                    # ワークスペース内のファイル → 相対パスを計算
                    try:
                        relative_path = str(path.absolute().relative_to(Path(workspace_cwd).absolute()))
                    except ValueError:
                        relative_path = path.name

                # 即座にS3にアップロード（workspace_serviceが利用可能な場合）
                if workspace_service and tenant_id and conversation_id:
                    try:
                        # ファイル内容を読み込み
                        file_content = path.read_bytes()
                        content_type = mime_type or "application/octet-stream"

                        # S3にアップロード
                        await workspace_service.s3.upload(
                            tenant_id,
                            conversation_id,
                            relative_path,
                            file_content,
                            content_type,
                        )

                        # DBに登録
                        await workspace_service.register_ai_file(
                            tenant_id,
                            conversation_id,
                            relative_path,
                            is_presented=True,
                        )

                        logger.info(
                            "ファイル提示: S3に即時アップロード完了",
                            file_path=relative_path,
                            size=len(file_content),
                        )
                    except Exception as upload_error:
                        logger.error(
                            "ファイル提示: S3アップロード失敗",
                            file_path=relative_path,
                            error=str(upload_error),
                        )
                        # アップロード失敗してもファイル情報は追加する

                files_info.append({
                    "path": str(path.absolute()),
                    "relative_path": relative_path,
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mime_type": mime_type or "application/octet-stream",
                    "exists": True
                })
                logger.info(
                    "ファイル提示: ファイル検出",
                    file_path=file_path,
                    full_path=str(path.absolute()),
                    relative_path=relative_path,
                    size=path.stat().st_size
                )
            else:
                files_info.append({
                    "path": full_path,
                    "relative_path": file_path,
                    "name": os.path.basename(file_path),
                    "exists": False
                })
                logger.warning(
                    "ファイル提示: ファイルが存在しない",
                    file_path=file_path,
                    full_path=full_path
                )

        # 存在するファイルのみをフィルタ
        existing_files = [f for f in files_info if f.get("exists")]
        missing_files = [f for f in files_info if not f.get("exists")]

        # 結果メッセージを構築
        if existing_files:
            result_text = f"ファイルを提示しました: {description}\n\n"
            result_text += "【提示されたファイル】\n"
            for f in existing_files:
                result_text += f"• {f['name']} ({f['size']} bytes)\n"
                result_text += f"  ダウンロードパス: {f['relative_path']}\n"
        else:
            result_text = f"提示するファイルが見つかりませんでした: {description}\n\n"

        if missing_files:
            result_text += "\n【見つからなかったファイル】\n"
            for f in missing_files:
                result_text += f"• {f['relative_path']}"
                if f.get("error"):
                    result_text += f" ({f['error']})"
                result_text += "\n"

        return {
            "content": [{
                "type": "text",
                "text": result_text.strip()
            }],
            # メタデータとして追加情報を返す
            "_metadata": {
                "files": files_info,
                "presented_files": existing_files,
                "description": description
            }
        }

    return present_files_handler


def get_builtin_tool_definition(tool_name: str) -> dict[str, Any] | None:
    """
    ビルトインツールの定義を取得

    Args:
        tool_name: ツール名

    Returns:
        ツール定義（存在しない場合はNone）
    """
    return BUILTIN_TOOL_DEFINITIONS.get(tool_name)


def get_all_builtin_tool_definitions() -> list[dict[str, Any]]:
    """
    全ビルトインツールの定義を取得

    Returns:
        ツール定義のリスト
    """
    return list(BUILTIN_TOOL_DEFINITIONS.values())


def create_file_presentation_mcp_server(
    workspace_cwd: str = "",
    workspace_service=None,
    tenant_id: str = "",
    conversation_id: str = "",
):
    """
    ファイル提示用のSDK MCPサーバーを作成

    Args:
        workspace_cwd: ワークスペースのカレントディレクトリ
        workspace_service: WorkspaceServiceインスタンス（即時S3アップロード用）
        tenant_id: テナントID
        conversation_id: 会話ID

    Returns:
        SDK MCPサーバー設定
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping builtin MCP server")
        return None

    # present_filesツールを定義
    @tool(
        "present_files",
        "AIが作成・編集したファイルをユーザーに提示する。Write/Edit/NotebookEditでファイルを作成・編集した後は、必ずこのツールを使用してユーザーにファイルを提示してください。",
        {
            "file_paths": list[str],
            "description": str
        }
    )
    async def present_files_tool(args: dict[str, Any]) -> dict[str, Any]:
        """
        ファイルパスのリストを受け取り、ユーザーに提示する情報を返す
        """
        handler = create_present_files_handler(
            workspace_cwd,
            workspace_service,
            tenant_id,
            conversation_id,
        )
        return await handler(args)

    # MCPサーバーとして登録
    server = create_sdk_mcp_server(
        name="file-presentation",
        version="1.0.0",
        tools=[present_files_tool]
    )

    return server


# ファイル提示ツールに関するシステムプロンプト
FILE_PRESENTATION_PROMPT = """
## ファイル作成ルール
- **相対パスのみ使用**（例: `hello.py`）。絶対パス（/tmp/等）は禁止
- ファイル作成後は `mcp__file-presentation__present_files` で提示
- file_paths は配列で指定: `["hello.py"]`
- **サブエージェント（Task）がファイルを作成した場合も、その完了後に必ず `mcp__file-presentation__present_files` を呼び出してください**
- サブエージェントの結果からファイルパスを確認し、作成されたファイルを提示してください
"""


def create_file_tools_mcp_server(
    workspace_service,
    tenant_id: str,
    conversation_id: str,
):
    """
    ファイル読み込み用のSDK MCPサーバーを作成（新版）

    Args:
        workspace_service: WorkspaceServiceインスタンス
        tenant_id: テナントID
        conversation_id: 会話ID

    Returns:
        SDK MCPサーバー設定
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping file tools MCP server")
        return None

    from app.services.workspace.file_tools import create_file_tools_handlers

    handlers = create_file_tools_handlers(workspace_service, tenant_id, conversation_id)

    # 共通ツール
    @tool(
        "list_workspace_files",
        "ワークスペース内のファイル一覧を取得します。filter_typeで絞り込み可能（image/pdf/office/text/all）。",
        {"filter_type": str},
    )
    async def list_workspace_files_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["list_workspace_files"](args)

    @tool(
        "read_image_file",
        "画像ファイルを視覚的に読み込みます（image content block）。file_pathでパスを指定。max_dimensionでリサイズ上限を指定可能（デフォルト: 1920）。",
        {"file_path": str, "max_dimension": int},
    )
    async def read_image_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["read_image_file"](args)

    # Excel ツール
    @tool(
        "inspect_excel_file",
        "Excelファイルの構造を確認します。シート一覧、ヘッダー行、データサンプルを返します。",
        {"file_path": str},
    )
    async def inspect_excel_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["inspect_excel_file"](args)

    @tool(
        "read_excel_sheet",
        "Excelシートのデータを取得します。sheet_name（シート名）、start_row/end_row（行範囲）、columns（列指定: 'A:D'や'A,C,E'）を指定可能。",
        {"file_path": str, "sheet_name": str, "start_row": int, "end_row": int, "columns": str},
    )
    async def read_excel_sheet_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["read_excel_sheet"](args)

    # PDF ツール
    @tool(
        "inspect_pdf_file",
        "PDFファイルの構造を確認します。ページ数、目次、各ページの概要を返します。",
        {"file_path": str},
    )
    async def inspect_pdf_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["inspect_pdf_file"](args)

    @tool(
        "read_pdf_pages",
        "PDFページのテキストを抽出します。pagesで範囲指定（'1-5'や'1,3,5'形式）。",
        {"file_path": str, "pages": str},
    )
    async def read_pdf_pages_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["read_pdf_pages"](args)

    @tool(
        "convert_pdf_to_images",
        "PDFページを画像に変換してワークスペースに保存します。pagesで範囲指定（最大5ページ）、dpiで解像度指定（デフォルト: 150）。保存されたパスを返します。",
        {"file_path": str, "pages": str, "dpi": int},
    )
    async def convert_pdf_to_images_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["convert_pdf_to_images"](args)

    # Word ツール
    @tool(
        "inspect_word_file",
        "Wordファイルの構造を確認します。見出し一覧、段落数、冒頭プレビューを返します。",
        {"file_path": str},
    )
    async def inspect_word_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["inspect_word_file"](args)

    @tool(
        "read_word_section",
        "Wordファイルのセクションを取得します。headingで見出しを指定するか、start_paragraph/end_paragraphで段落範囲を指定。",
        {"file_path": str, "heading": str, "start_paragraph": int, "end_paragraph": int},
    )
    async def read_word_section_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["read_word_section"](args)

    # PowerPoint ツール
    @tool(
        "inspect_pptx_file",
        "PowerPointファイルの構造を確認します。スライド一覧、各スライドの要素数を返します。",
        {"file_path": str},
    )
    async def inspect_pptx_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["inspect_pptx_file"](args)

    @tool(
        "read_pptx_slides",
        "PowerPointスライドのテキストを取得します。slidesで範囲指定（'1-5'や'1,3,5'形式）、include_notesでノートを含めるか指定。",
        {"file_path": str, "slides": str, "include_notes": bool},
    )
    async def read_pptx_slides_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["read_pptx_slides"](args)

    # 画像 ツール
    @tool(
        "inspect_image_file",
        "画像ファイルのメタデータを確認します。解像度、ファイルサイズ、EXIF情報を返します。",
        {"file_path": str},
    )
    async def inspect_image_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["inspect_image_file"](args)

    # MCPサーバーとして登録
    server = create_sdk_mcp_server(
        name="file-tools",
        version="2.0.0",
        tools=[
            # 共通
            list_workspace_files_tool,
            read_image_file_tool,
            # Excel
            inspect_excel_file_tool,
            read_excel_sheet_tool,
            # PDF
            inspect_pdf_file_tool,
            read_pdf_pages_tool,
            convert_pdf_to_images_tool,
            # Word
            inspect_word_file_tool,
            read_word_section_tool,
            # PowerPoint
            inspect_pptx_file_tool,
            read_pptx_slides_tool,
            # 画像
            inspect_image_file_tool,
        ],
    )

    return server
