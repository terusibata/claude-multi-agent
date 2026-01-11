"""
SSEストリーミングユーティリティ
Server-Sent Events形式でのストリーミング送信
"""
import json
from typing import Any, AsyncIterator

from sse_starlette.sse import ServerSentEvent


def generate_sse_event(event: str, data: dict[str, Any]) -> ServerSentEvent:
    """
    SSEイベントを生成

    Args:
        event: イベントタイプ
        data: イベントデータ

    Returns:
        ServerSentEventオブジェクト
    """
    return ServerSentEvent(
        event=event,
        data=json.dumps(data, ensure_ascii=False, default=str),
    )


async def send_sse_event(
    event: str,
    data: dict[str, Any],
) -> str:
    """
    SSEイベントを文字列形式で生成

    Args:
        event: イベントタイプ
        data: イベントデータ

    Returns:
        SSE形式の文字列
    """
    json_data = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {json_data}\n\n"


async def sse_event_generator(
    event: str,
    data: dict[str, Any],
) -> AsyncIterator[dict]:
    """
    SSEイベントジェネレータ

    Args:
        event: イベントタイプ
        data: イベントデータ

    Yields:
        SSEイベント辞書
    """
    yield {
        "event": event,
        "data": json.dumps(data, ensure_ascii=False, default=str),
    }


def format_session_start_event(
    session_id: str,
    tools: list[str],
    model: str,
) -> dict:
    """
    セッション開始イベントをフォーマット

    Args:
        session_id: セッションID
        tools: 利用可能なツールリスト
        model: 使用モデル

    Returns:
        イベントデータ
    """
    return {
        "event": "session_start",
        "data": {
            "session_id": session_id,
            "tools": tools,
            "model": model,
        },
    }


def format_text_delta_event(text: str) -> dict:
    """
    テキスト増分イベントをフォーマット

    Args:
        text: テキスト増分

    Returns:
        イベントデータ
    """
    return {
        "event": "text_delta",
        "data": {
            "text": text,
        },
    }


def format_tool_start_event(
    tool_use_id: str,
    tool_name: str,
    summary: str,
    tool_input: dict | None = None,
) -> dict:
    """
    ツール開始イベントをフォーマット

    Args:
        tool_use_id: ツール使用ID
        tool_name: ツール名
        summary: サマリー
        tool_input: ツール入力パラメータ

    Returns:
        イベントデータ
    """
    data = {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "summary": summary,
    }
    if tool_input is not None:
        # 大きな入力値は切り詰める
        truncated_input = {}
        for key, value in tool_input.items():
            if isinstance(value, str) and len(value) > 500:
                truncated_input[key] = value[:500] + "..."
            else:
                truncated_input[key] = value
        data["tool_input"] = truncated_input
    return {
        "event": "tool_start",
        "data": data,
    }


def format_tool_complete_event(
    tool_use_id: str,
    tool_name: str,
    status: str,
    summary: str,
    result_preview: str | None = None,
    is_error: bool = False,
) -> dict:
    """
    ツール完了イベントをフォーマット

    Args:
        tool_use_id: ツール使用ID
        tool_name: ツール名
        status: ステータス
        summary: サマリー
        result_preview: 結果のプレビュー
        is_error: エラーかどうか

    Returns:
        イベントデータ
    """
    data = {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "status": status,
        "summary": summary,
        "is_error": is_error,
    }
    if result_preview is not None:
        data["result_preview"] = result_preview
    return {
        "event": "tool_complete",
        "data": data,
    }


def format_thinking_event(content: str) -> dict:
    """
    思考プロセスイベントをフォーマット

    Args:
        content: 思考内容

    Returns:
        イベントデータ
    """
    return {
        "event": "thinking",
        "data": {
            "content": content,
        },
    }


def format_result_event(
    subtype: str,
    result: str | None,
    errors: list[str] | None,
    usage: dict,
    cost_usd: float,
    num_turns: int,
    duration_ms: int,
    tools_summary: list[dict],
    session_id: str | None = None,
    files_presented: list[dict] | None = None,
) -> dict:
    """
    結果イベントをフォーマット

    Args:
        subtype: サブタイプ (success / error_during_execution)
        result: 結果テキスト
        errors: エラーリスト
        usage: 使用状況
        cost_usd: コスト（USD）
        num_turns: ターン数
        duration_ms: 実行時間（ミリ秒）
        tools_summary: ツール使用サマリー
        session_id: セッションID
        files_presented: 今回の処理で提供されたファイル一覧

    Returns:
        イベントデータ
    """
    data = {
        "subtype": subtype,
        "result": result,
        "errors": errors,
        "usage": usage,
        "cost_usd": cost_usd,
        "num_turns": num_turns,
        "duration_ms": duration_ms,
        "tools_summary": tools_summary,
    }
    if session_id is not None:
        data["session_id"] = session_id
    if files_presented is not None:
        data["files_presented"] = files_presented
    return {
        "event": "result",
        "data": data,
    }


def format_error_event(error_message: str, error_type: str = "error") -> dict:
    """
    エラーイベントをフォーマット

    Args:
        error_message: エラーメッセージ
        error_type: エラータイプ

    Returns:
        イベントデータ
    """
    return {
        "event": "error",
        "data": {
            "type": error_type,
            "message": error_message,
        },
    }


def format_title_generated_event(title: str) -> dict:
    """
    タイトル生成イベントをフォーマット

    Args:
        title: 生成されたタイトル

    Returns:
        イベントデータ
    """
    return {
        "event": "title_generated",
        "data": {
            "title": title,
        },
    }


def format_files_presented_event(
    files: list[dict],
    message: str = "",
) -> dict:
    """
    ファイル提示イベントをフォーマット
    AIがユーザーに提供するファイルを通知

    Args:
        files: ファイル情報のリスト
            [{"file_path": "...", "file_name": "...", "file_size": 123, "description": "..."}]
        message: AIからのメッセージ

    Returns:
        イベントデータ
    """
    return {
        "event": "files_presented",
        "data": {
            "files": files,
            "message": message,
        },
    }
