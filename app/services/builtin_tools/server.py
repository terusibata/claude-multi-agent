"""
MCPサーバー生成

ファイル提示およびファイルツール用のSDK MCPサーバーを作成する。
"""
from typing import Any

import structlog

from app.services.builtin_tools.file_presentation import create_present_files_handler

logger = structlog.get_logger(__name__)


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
        # オプション依存: claude_agent_sdkが未インストールの場合はスキップ
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
        # オプション依存: claude_agent_sdkが未インストールの場合はスキップ
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
