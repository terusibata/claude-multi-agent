"""
進捗表示メッセージテンプレート

ユーザーフレンドリーな進捗メッセージを提供
"""

import random
from typing import Optional


# フェーズ別のメッセージテンプレート（ランダムで選択）
PHASE_MESSAGES: dict[str, list[str]] = {
    "thinking": [
        "考えています...",
        "回答を検討中...",
        "最適な回答を考えています...",
        "内容を分析しています...",
        "リクエストを処理中...",
    ],
    "generating": [
        "回答を作成しています...",
        "テキストを生成中...",
        "回答を準備しています...",
        "内容を整理しています...",
    ],
    # ツール系は別途定義
}

# 待機中メッセージ（3秒以上経過時）
WAITING_MESSAGES: dict[str, list[str]] = {
    "thinking": [
        "回答を検討中です...",
        "もう少々お待ちください...",
        "内容を精査しています...",
        "最適な回答を探しています...",
    ],
    "generating": [
        "テキスト生成を待機中...",
        "回答の作成に時間がかかっています...",
        "もう少々お待ちください...",
    ],
    "tool": [
        "{tool_label}の処理を待機中...",
        "{tool_label}に時間がかかっています...",
        "{tool_label}の完了を待っています...",
        "処理中です。もう少々お待ちください...",
    ],
}

# 組み込みツール別の初期メッセージ
BUILTIN_TOOL_MESSAGES: dict[str, list[str]] = {
    # Claude Agent SDK 組み込みツール
    "Read": [
        "ファイルを読み込んでいます...",
        "ファイル内容を取得中...",
    ],
    "Write": [
        "ファイルを作成しています...",
        "ファイルに書き込んでいます...",
    ],
    "Edit": [
        "ファイルを編集しています...",
        "コードを修正中...",
    ],
    "Bash": [
        "コマンドを実行しています...",
        "ターミナル処理を実行中...",
    ],
    "Glob": [
        "ファイルを検索しています...",
        "パターンに一致するファイルを探しています...",
    ],
    "Grep": [
        "テキストを検索しています...",
        "コード内を検索中...",
    ],
    "Task": [
        "サブタスクを処理しています...",
        "並行処理を実行中...",
    ],
    "WebFetch": [
        "Webページを取得しています...",
        "Web情報を読み込み中...",
    ],
    "WebSearch": [
        "Web検索を実行しています...",
        "インターネットで情報を検索中...",
    ],
    "TodoRead": [
        "タスク一覧を確認しています...",
    ],
    "TodoWrite": [
        "タスクを更新しています...",
    ],
    "NotebookEdit": [
        "ノートブックを編集しています...",
    ],
    # 組み込みMCPツール（file-presentation）
    "mcp__file-presentation__present_files": [
        "ファイルを提示しています...",
        "結果ファイルを準備中...",
    ],
    # 組み込みMCPツール（file-tools）
    "mcp__file-tools__list_workspace_files": [
        "ワークスペースのファイル一覧を取得中...",
    ],
    "mcp__file-tools__read_image_file": [
        "画像ファイルを読み込んでいます...",
        "画像を分析中...",
    ],
    "mcp__file-tools__inspect_excel_file": [
        "Excelファイルの構造を確認中...",
    ],
    "mcp__file-tools__read_excel_sheet": [
        "Excelデータを取得しています...",
        "スプレッドシートを読み込み中...",
    ],
    "mcp__file-tools__inspect_pdf_file": [
        "PDFファイルの構造を確認中...",
    ],
    "mcp__file-tools__read_pdf_pages": [
        "PDFテキストを抽出しています...",
        "PDFを読み込み中...",
    ],
    "mcp__file-tools__convert_pdf_to_images": [
        "PDFを画像に変換しています...",
        "PDF画像化処理中...",
    ],
    "mcp__file-tools__inspect_word_file": [
        "Wordファイルの構造を確認中...",
    ],
    "mcp__file-tools__read_word_section": [
        "Wordテキストを取得しています...",
        "ドキュメントを読み込み中...",
    ],
    "mcp__file-tools__inspect_pptx_file": [
        "PowerPointの構造を確認中...",
    ],
    "mcp__file-tools__read_pptx_slides": [
        "PowerPointスライドを読み込んでいます...",
        "プレゼンテーションを処理中...",
    ],
    "mcp__file-tools__inspect_image_file": [
        "画像の情報を取得しています...",
    ],
}

# 汎用MCPツールのデフォルトメッセージ
DEFAULT_MCP_MESSAGES: list[str] = [
    "MCPツールを実行しています...",
    "外部ツールを処理中...",
    "ツールの応答を待っています...",
]

# 汎用ツールのデフォルトメッセージ（不明なツール用）
DEFAULT_TOOL_MESSAGES: list[str] = [
    "処理を実行しています...",
    "ツールを実行中...",
]


def get_initial_message(phase: str, tool_name: Optional[str] = None) -> str:
    """
    フェーズ開始時の初期メッセージを取得

    Args:
        phase: フェーズ名（thinking, generating, tool）
        tool_name: ツール名（phaseがtoolの場合）

    Returns:
        表示メッセージ
    """
    if phase == "tool" and tool_name:
        # 組み込みツール / 組み込みMCPツールの場合は専用メッセージ
        if tool_name in BUILTIN_TOOL_MESSAGES:
            return random.choice(BUILTIN_TOOL_MESSAGES[tool_name])

        # 動的MCPツールは汎用メッセージ
        if tool_name.startswith("mcp__"):
            return random.choice(DEFAULT_MCP_MESSAGES)

        # 不明なツールはデフォルト
        return random.choice(DEFAULT_TOOL_MESSAGES)

    # thinking/generatingフェーズ
    if phase in PHASE_MESSAGES:
        return random.choice(PHASE_MESSAGES[phase])

    return "処理中..."


def get_waiting_message(
    phase: str, tool_name: Optional[str] = None, tool_label: Optional[str] = None
) -> str:
    """
    待機中メッセージを取得（3秒以上経過時）

    Args:
        phase: フェーズ名（thinking, generating, tool）
        tool_name: ツール名（phaseがtoolの場合）
        tool_label: ツールの日本語ラベル

    Returns:
        表示メッセージ
    """
    if phase == "tool":
        messages = WAITING_MESSAGES.get("tool", [])
        if messages:
            message = random.choice(messages)
            # {tool_label}を置換
            label = tool_label or "ツール"
            return message.format(tool_label=label)
        return "処理中です。もう少々お待ちください..."

    if phase in WAITING_MESSAGES:
        return random.choice(WAITING_MESSAGES[phase])

    return "もう少々お待ちください..."
