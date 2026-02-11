"""
ビルトインMCPツールの実装
アプリケーション組み込みのMCPサーバーとツール
"""
import json
import os
import mimetypes
import shutil
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ビルトインツールの定義
BUILTIN_TOOL_DEFINITIONS = {
    "present_files": {
        "name": "present_files",
        "description": (
            "AIが作成・編集したファイルをユーザーに提示する。"
            "ファイルパスのリストと説明を受け取り、ユーザーに提示する情報を返す。"
            "Write/Edit/NotebookEditでファイルを作成・編集した後は、"
            "必ずこのツールを使用してユーザーにファイルを提示してください。"
        ),
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


# ファイル提示ツールに関するシステムプロンプト
FILE_PRESENTATION_PROMPT = """
## ファイル作成ルール
- **相対パスのみ使用**（例: `hello.py`）。絶対パス（/tmp/等）は禁止
- ファイル作成後は `mcp__file-presentation__present_files` で提示
- file_paths は配列で指定: `["hello.py"]`
- **サブエージェント（Task）がファイルを作成した場合も、その完了後に必ず `mcp__file-presentation__present_files` を呼び出してください**
- サブエージェントの結果からファイルパスを確認し、作成されたファイルを提示してください
"""


# =============================================================================
# file_paths 正規化
# =============================================================================


def _normalize_file_paths(file_paths_input: Any) -> list[str]:
    """
    file_pathsを正規化してリスト形式にする

    LLMが文字列やJSON文字列で返す場合にも対応。

    Args:
        file_paths_input: ファイルパス入力（リスト、文字列、JSON文字列）

    Returns:
        正規化されたファイルパスのリスト
    """
    if isinstance(file_paths_input, list):
        return file_paths_input

    if not isinstance(file_paths_input, str):
        return []

    # JSON配列文字列の場合はパース
    if file_paths_input.startswith("["):
        try:
            parsed = json.loads(file_paths_input)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            logger.debug("JSONパースフォールバック", exc_info=True)

    return [file_paths_input]


# =============================================================================
# ファイルパス解決
# =============================================================================


def _resolve_file_path(file_path: str, workspace_cwd: str) -> str:
    """
    ファイルパスをフルパスに解決

    Args:
        file_path: 入力パス（相対/絶対）
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        解決されたフルパス
    """
    if not os.path.isabs(file_path) and workspace_cwd:
        return os.path.join(workspace_cwd, file_path)
    return file_path


def _compute_relative_path(path: Path, file_path: str, workspace_cwd: str) -> str:
    """
    ファイルのワークスペース相対パスを計算

    ワークスペース外の場合はファイル名を返す。

    Args:
        path: Pathオブジェクト
        file_path: 元の入力パス
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        相対パス
    """
    if not workspace_cwd:
        return file_path

    abs_path = str(path.absolute())
    abs_cwd = str(Path(workspace_cwd).absolute())

    if not abs_path.startswith(abs_cwd):
        # ワークスペース外
        return file_path

    try:
        return str(path.absolute().relative_to(Path(workspace_cwd).absolute()))
    except ValueError:
        return path.name


# =============================================================================
# ワークスペース外ファイルのコピー処理
# =============================================================================


def _copy_file_to_workspace(path: Path, workspace_cwd: str) -> tuple[Path, str] | None:
    """
    ワークスペース外のファイルをワークスペース内にコピー

    同名ファイルが存在する場合はユニークな名前を生成。

    Args:
        path: コピー元のパス
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        (コピー先パス, 相対パス) のタプル。失敗時はNone。
    """
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
        logger.info(
            "ファイル提示: ワークスペース外ファイルをコピー",
            source=str(path),
            destination=str(dest_path),
        )
        return dest_path, dest_path.name
    except Exception as e:
        logger.error(
            "ファイル提示: ファイルコピー失敗",
            source=str(path),
            error=str(e),
        )
        return None


# =============================================================================
# S3アップロード処理
# =============================================================================


async def _upload_to_s3(
    workspace_service,
    tenant_id: str,
    conversation_id: str,
    relative_path: str,
    path: Path,
    mime_type: str | None,
) -> None:
    """
    ファイルをS3にアップロードしてDBに登録

    Args:
        workspace_service: WorkspaceServiceインスタンス
        tenant_id: テナントID
        conversation_id: 会話ID
        relative_path: S3上の相対パス
        path: ローカルファイルのパス
        mime_type: MIMEタイプ
    """
    file_content = path.read_bytes()
    content_type = mime_type or "application/octet-stream"

    await workspace_service.s3.upload(
        tenant_id, conversation_id, relative_path, file_content, content_type,
    )
    await workspace_service.register_ai_file(
        tenant_id, conversation_id, relative_path, is_presented=True,
    )

    logger.info(
        "ファイル提示: S3に即時アップロード完了",
        file_path=relative_path,
        size=len(file_content),
    )


# =============================================================================
# 結果メッセージの構築
# =============================================================================


def _build_result_message(
    existing_files: list[dict],
    missing_files: list[dict],
    description: str,
) -> str:
    """
    ファイル提示の結果メッセージを構築

    Args:
        existing_files: 存在するファイル情報のリスト
        missing_files: 見つからなかったファイル情報のリスト
        description: ファイルの説明

    Returns:
        結果メッセージ文字列
    """
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
            line = f"• {f['relative_path']}"
            if f.get("error"):
                line += f" ({f['error']})"
            parts.append(line)

    return "\n".join(parts)


# =============================================================================
# present_files ハンドラー
# =============================================================================


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
        """present_filesツールの実行ハンドラー"""
        file_paths = _normalize_file_paths(args.get("file_paths", []))
        description = args.get("description", "")

        files_info = []
        for file_path in file_paths:
            full_path = _resolve_file_path(file_path, workspace_cwd)
            path = Path(full_path)

            if not (path.exists() and path.is_file()):
                files_info.append({
                    "path": full_path,
                    "relative_path": file_path,
                    "name": os.path.basename(file_path),
                    "exists": False,
                })
                logger.warning("ファイル提示: ファイルが存在しない", file_path=file_path, full_path=full_path)
                continue

            mime_type, _ = mimetypes.guess_type(str(path))
            relative_path = file_path

            # ワークスペース外のファイルはワークスペース内にコピー
            is_outside_workspace = (
                workspace_cwd
                and not str(path.absolute()).startswith(str(Path(workspace_cwd).absolute()))
            )

            if is_outside_workspace:
                copy_result = _copy_file_to_workspace(path, workspace_cwd)
                if copy_result is None:
                    files_info.append({
                        "path": full_path,
                        "relative_path": file_path,
                        "name": path.name,
                        "exists": False,
                        "error": "ワークスペースへのコピーに失敗",
                    })
                    continue
                path, relative_path = copy_result
            else:
                relative_path = _compute_relative_path(path, file_path, workspace_cwd)

            # S3にアップロード（workspace_serviceが利用可能な場合）
            if workspace_service and tenant_id and conversation_id:
                try:
                    await _upload_to_s3(
                        workspace_service, tenant_id, conversation_id,
                        relative_path, path, mime_type,
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
                "exists": True,
            })

        existing_files = [f for f in files_info if f.get("exists")]
        missing_files = [f for f in files_info if not f.get("exists")]

        result_text = _build_result_message(existing_files, missing_files, description)

        return {
            "content": [{"type": "text", "text": result_text}],
            "_metadata": {
                "files": files_info,
                "presented_files": existing_files,
                "description": description,
            },
        }

    return present_files_handler


# =============================================================================
# ビルトインツール定義の取得
# =============================================================================


def get_builtin_tool_definition(tool_name: str) -> dict[str, Any] | None:
    """ビルトインツールの定義を取得"""
    return BUILTIN_TOOL_DEFINITIONS.get(tool_name)


def get_all_builtin_tool_definitions() -> list[dict[str, Any]]:
    """全ビルトインツールの定義を取得"""
    return list(BUILTIN_TOOL_DEFINITIONS.values())


# =============================================================================
# MCPサーバー生成
# =============================================================================


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
        logger.warning("claude_agent_sdk が利用不可のためビルトインMCPサーバーをスキップ")
        return None

    handler = create_present_files_handler(
        workspace_cwd, workspace_service, tenant_id, conversation_id,
    )

    @tool(
        "present_files",
        "AIが作成・編集したファイルをユーザーに提示する。"
        "Write/Edit/NotebookEditでファイルを作成・編集した後は、"
        "必ずこのツールを使用してユーザーにファイルを提示してください。",
        {"file_paths": list[str], "description": str},
    )
    async def present_files_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handler(args)

    server = create_sdk_mcp_server(
        name="file-presentation",
        version="1.0.0",
        tools=[present_files_tool],
    )
    return server


def create_file_tools_mcp_server(
    workspace_service,
    tenant_id: str,
    conversation_id: str,
):
    """
    ファイル読み込み用のSDK MCPサーバーを作成

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
        logger.warning("claude_agent_sdk が利用不可のためファイルツールMCPサーバーをスキップ")
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
        "get_sheet_info",
        "Excelファイルのシート情報を取得します。シート一覧、各シートの行数・列数・範囲、印刷領域の有無を返します。",
        {"file_path": str},
    )
    async def get_sheet_info_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_sheet_info"](args)

    @tool(
        "get_sheet_csv",
        "指定シートの内容をCSV Markdown形式で取得します。sheet_name（シート名、必須）、start_row/end_row（行範囲）、max_rows（最大行数、デフォルト100）、use_print_area（印刷領域を使用するか、デフォルトtrue）を指定可能。",
        {"file_path": str, "sheet_name": str, "start_row": int, "end_row": int, "max_rows": int, "use_print_area": bool},
    )
    async def get_sheet_csv_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_sheet_csv"](args)

    @tool(
        "search_workbook",
        "Excelワークブック全体からキーワード検索を行います。query（検索キーワード、必須）、case_sensitive（大文字小文字区別、デフォルトfalse）、max_hits（最大ヒット数、デフォルト50）を指定可能。",
        {"file_path": str, "query": str, "case_sensitive": bool, "max_hits": int},
    )
    async def search_workbook_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["search_workbook"](args)

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
        "get_document_info",
        "Wordファイルの構造情報を取得します。見出し構造、段落数、文字数、表の概要を返します。",
        {"file_path": str},
    )
    async def get_document_info_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_document_info"](args)

    @tool(
        "get_document_content",
        "Wordファイルの内容を取得します。headingで見出しセクション指定、またはstart_paragraph/max_paragraphsで範囲指定。include_tablesで表を含めるか指定。",
        {"file_path": str, "heading": str, "start_paragraph": int, "end_paragraph": int, "max_paragraphs": int, "include_tables": bool},
    )
    async def get_document_content_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_document_content"](args)

    @tool(
        "search_document",
        "Wordドキュメント全体からキーワード検索を行います。段落、表、見出しすべてを対象に検索。query（検索キーワード、必須）、case_sensitive、max_hits、include_tablesを指定可能。",
        {"file_path": str, "query": str, "case_sensitive": bool, "max_hits": int, "include_tables": bool},
    )
    async def search_document_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["search_document"](args)

    # PowerPoint ツール
    @tool(
        "get_presentation_info",
        "PowerPointファイルの構造情報を取得します。スライド一覧、各スライドの要素数、文字数を返します。",
        {"file_path": str},
    )
    async def get_presentation_info_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_presentation_info"](args)

    @tool(
        "get_slides_content",
        "PowerPointスライドの内容を取得します。slidesで範囲指定（'1-5'や'1,3,5'形式）、max_slidesで最大取得数指定、include_notes/include_tablesで含める内容を指定。",
        {"file_path": str, "slides": str, "max_slides": int, "include_notes": bool, "include_tables": bool},
    )
    async def get_slides_content_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["get_slides_content"](args)

    @tool(
        "search_presentation",
        "PowerPointプレゼンテーション全体からキーワード検索を行います。スライドテキスト、表、ノートすべてを対象に検索。query（検索キーワード、必須）、case_sensitive、max_hits、include_notesを指定可能。",
        {"file_path": str, "query": str, "case_sensitive": bool, "max_hits": int, "include_notes": bool},
    )
    async def search_presentation_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["search_presentation"](args)

    # 画像 ツール
    @tool(
        "inspect_image_file",
        "画像ファイルのメタデータを確認します。解像度、ファイルサイズ、EXIF情報を返します。",
        {"file_path": str},
    )
    async def inspect_image_file_tool(args: dict[str, Any]) -> dict[str, Any]:
        return await handlers["inspect_image_file"](args)

    server = create_sdk_mcp_server(
        name="file-tools",
        version="2.0.0",
        tools=[
            # 共通
            list_workspace_files_tool,
            read_image_file_tool,
            # Excel
            get_sheet_info_tool,
            get_sheet_csv_tool,
            search_workbook_tool,
            # PDF
            inspect_pdf_file_tool,
            read_pdf_pages_tool,
            convert_pdf_to_images_tool,
            # Word
            get_document_info_tool,
            get_document_content_tool,
            search_document_tool,
            # PowerPoint
            get_presentation_info_tool,
            get_slides_content_tool,
            search_presentation_tool,
            # 画像
            inspect_image_file_tool,
        ],
    )
    return server
