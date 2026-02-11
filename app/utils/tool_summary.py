"""
ツールサマリー生成ユーティリティ
エージェントが使用したツールの簡潔なサマリーを生成
"""
from typing import Any


def generate_tool_summary(tool_name: str, tool_input: dict[str, Any]) -> str:
    """
    ツール使用の簡潔なサマリーを生成

    Args:
        tool_name: ツール名
        tool_input: ツール入力パラメータ

    Returns:
        簡潔なサマリー文字列
    """
    if tool_name == "Read":
        # ファイル読み込み
        path = tool_input.get("file_path", tool_input.get("path", "unknown"))
        return f"ファイル読み込み: {path}"

    elif tool_name == "Write":
        # ファイル書き込み
        path = tool_input.get("file_path", tool_input.get("path", "unknown"))
        return f"ファイル書き込み: {path}"

    elif tool_name == "Edit":
        # ファイル編集
        path = tool_input.get("file_path", tool_input.get("path", "unknown"))
        return f"ファイル編集: {path}"

    elif tool_name == "Bash":
        # コマンド実行
        cmd = tool_input.get("command", "")
        if len(cmd) > 50:
            return f"コマンド実行: {cmd[:50]}..."
        return f"コマンド実行: {cmd}"

    elif tool_name == "Glob":
        # ファイル検索
        pattern = tool_input.get("pattern", "unknown")
        return f"ファイル検索: {pattern}"

    elif tool_name == "Grep":
        # テキスト検索
        pattern = tool_input.get("pattern", "unknown")
        return f"テキスト検索: {pattern}"

    elif tool_name == "WebFetch":
        # Web取得
        url = tool_input.get("url", "unknown")
        if len(url) > 50:
            return f"Web取得: {url[:50]}..."
        return f"Web取得: {url}"

    elif tool_name == "WebSearch":
        # Web検索
        query = tool_input.get("query", "unknown")
        return f"Web検索: {query}"

    elif tool_name.startswith("mcp__"):
        # MCPツールの場合
        parts = tool_name.split("__", 2)
        if len(parts) >= 3:
            server = parts[1]
            action = parts[2]
            return f"{server}: {action}"
        return f"MCP: {tool_name}"

    elif tool_name == "Skill":
        # Skill使用
        skill_name = tool_input.get("skill", tool_input.get("skill_name", "unknown"))
        return f"スキル使用: {skill_name}"

    elif tool_name == "Task":
        # タスク実行
        description = tool_input.get("description", "サブタスク")
        return f"タスク: {description}"

    else:
        # その他のツール
        return f"ツール使用: {tool_name}"


def generate_tool_result_summary(
    tool_name: str,
    status: str,
    output: Any | None = None,
) -> str:
    """
    ツール実行結果の簡潔なサマリーを生成

    Args:
        tool_name: ツール名
        status: 実行ステータス (completed / error)
        output: ツール出力

    Returns:
        簡潔なサマリー文字列
    """
    if status == "error":
        return "失敗"

    if tool_name == "Read":
        # ファイル読み込み結果
        if output and isinstance(output, str):
            lines = len(output.split("\n"))
            return f"{lines}行を読み込み"
        return "読み込み完了"

    elif tool_name == "Write":
        return "ファイル書き込み完了"

    elif tool_name == "Edit":
        return "ファイル編集完了"

    elif tool_name == "Bash":
        return "コマンド実行完了"

    elif tool_name == "Glob":
        # ファイル検索結果
        if output and isinstance(output, list):
            return f"{len(output)}件のファイルを発見"
        return "検索完了"

    elif tool_name == "Grep":
        # テキスト検索結果
        if output and isinstance(output, list):
            return f"{len(output)}件のマッチ"
        return "検索完了"

    elif tool_name == "WebFetch":
        return "取得完了"

    elif tool_name == "WebSearch":
        return "検索完了"

    elif tool_name.startswith("mcp__"):
        return "完了"

    elif tool_name == "Skill":
        return "スキル実行完了"

    elif tool_name == "Task":
        return "タスク完了"

    else:
        return "完了"


def format_tool_for_display(
    tool_name: str,
    tool_input: dict[str, Any],
    status: str,
    output: Any | None = None,
) -> dict[str, str]:
    """
    表示用にツール情報をフォーマット

    Args:
        tool_name: ツール名
        tool_input: ツール入力
        status: 実行ステータス
        output: ツール出力

    Returns:
        フォーマットされたツール情報
    """
    return {
        "tool_name": tool_name,
        "summary": generate_tool_summary(tool_name, tool_input),
        "status": status,
        "result_summary": generate_tool_result_summary(tool_name, status, output),
    }
