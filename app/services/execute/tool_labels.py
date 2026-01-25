"""
ツール名の日本語ラベルマッピング

ユーザーフレンドリーな表示用
"""

from typing import Optional


# 組み込みツールのラベル
BUILTIN_TOOL_LABELS: dict[str, str] = {
    # Claude Agent SDK 組み込みツール
    "Read": "ファイル読み込み",
    "Write": "ファイル作成",
    "Edit": "ファイル編集",
    "Bash": "コマンド実行",
    "Glob": "ファイル検索",
    "Grep": "テキスト検索",
    "Task": "サブタスク",
    "WebFetch": "Web取得",
    "WebSearch": "Web検索",
    "TodoRead": "タスク確認",
    "TodoWrite": "タスク更新",
    "NotebookEdit": "ノートブック編集",
}

# 組み込みMCPツールのラベル
BUILTIN_MCP_LABELS: dict[str, str] = {
    # file-presentation
    "mcp__file-presentation__present_files": "ファイル提示",
    # file-reader
    "mcp__file-reader__read_image_file": "画像読み込み",
    "mcp__file-reader__read_pdf_file": "PDF読み込み",
    "mcp__file-reader__read_office_file": "Officeファイル読み込み",
    "mcp__file-reader__list_workspace_files": "ファイル一覧取得",
}


def get_tool_label(tool_name: str) -> str:
    """
    ツール名からユーザーフレンドリーなラベルを取得

    Args:
        tool_name: ツール名

    Returns:
        日本語ラベル
    """
    # 組み込みツール
    if tool_name in BUILTIN_TOOL_LABELS:
        return BUILTIN_TOOL_LABELS[tool_name]

    # 組み込みMCPツール
    if tool_name in BUILTIN_MCP_LABELS:
        return BUILTIN_MCP_LABELS[tool_name]

    # 動的MCPツール
    if tool_name.startswith("mcp__"):
        # mcp__server-name__tool_name の形式から server-name を抽出
        parts = tool_name.split("__")
        if len(parts) >= 2:
            server_name = parts[1]
            return f"MCP ({server_name})"
        return "MCPツール"

    # 不明なツール
    return "ツール"


def is_builtin_tool(tool_name: str) -> bool:
    """
    組み込みツール（SDK + 組み込みMCP）かどうかを判定

    Args:
        tool_name: ツール名

    Returns:
        組み込みツールの場合True
    """
    return tool_name in BUILTIN_TOOL_LABELS or tool_name in BUILTIN_MCP_LABELS
