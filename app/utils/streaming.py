"""
SSEストリーミングユーティリティ
Server-Sent Events形式でのストリーミング送信

イベント形式はmessagesエンドポイント（/api/tenants/{tenant_id}/sessions/{session_id}/messages）
で取得できる形式と統一されています。

イベントタイプ:
- message: メッセージイベント（type: system/assistant/user_result/result）
- error: エラーイベント
- title_generated: タイトル生成イベント
"""
import json
from datetime import datetime
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


def _get_timestamp() -> str:
    """現在のタイムスタンプをISO形式で取得"""
    return datetime.utcnow().isoformat()


def format_system_message_event(
    subtype: str,
    data: dict[str, Any],
) -> dict:
    """
    システムメッセージイベントをフォーマット

    Args:
        subtype: サブタイプ (init / finish)
        data: データ

    Returns:
        イベントデータ
    """
    return {
        "event": "message",
        "data": {
            "type": "system",
            "subtype": subtype,
            "timestamp": _get_timestamp(),
            "data": data,
        },
    }


def format_assistant_message_event(
    content_blocks: list[dict[str, Any]],
) -> dict:
    """
    アシスタントメッセージイベントをフォーマット

    Args:
        content_blocks: コンテンツブロックのリスト

    Returns:
        イベントデータ
    """
    return {
        "event": "message",
        "data": {
            "type": "assistant",
            "subtype": None,
            "timestamp": _get_timestamp(),
            "content_blocks": content_blocks,
        },
    }


def format_user_result_message_event(
    content_blocks: list[dict[str, Any]],
) -> dict:
    """
    ユーザー結果メッセージイベントをフォーマット（ツール結果）

    Args:
        content_blocks: コンテンツブロックのリスト

    Returns:
        イベントデータ
    """
    return {
        "event": "message",
        "data": {
            "type": "user_result",
            "subtype": None,
            "timestamp": _get_timestamp(),
            "content_blocks": content_blocks,
        },
    }


def format_result_message_event(
    subtype: str,
    result: str | None,
    errors: list[str] | None,
    usage: dict,
    cost_usd: float,
    num_turns: int,
    duration_ms: int,
    session_id: str | None = None,
) -> dict:
    """
    結果メッセージイベントをフォーマット

    Args:
        subtype: サブタイプ (success / error_during_execution)
        result: 結果テキスト
        errors: エラーリスト
        usage: 使用状況
        cost_usd: コスト（USD）
        num_turns: ターン数
        duration_ms: 実行時間（ミリ秒）
        session_id: セッションID

    Returns:
        イベントデータ
    """
    data = {
        "type": "result",
        "subtype": subtype,
        "timestamp": _get_timestamp(),
        "result": result,
        "is_error": subtype != "success",
        "errors": errors,
        "usage": usage,
        "total_cost_usd": cost_usd,
        "num_turns": num_turns,
        "duration_ms": duration_ms,
    }
    if session_id is not None:
        data["session_id"] = session_id
    return {
        "event": "message",
        "data": data,
    }


# 以下は後方互換性のためのエイリアス関数
# 新しいコードではformat_*_message_event関数を使用してください

def format_session_start_event(
    session_id: str,
    tools: list[str],
    model: str,
) -> dict:
    """
    セッション開始イベントをフォーマット（messages形式）

    Args:
        session_id: セッションID
        tools: 利用可能なツールリスト
        model: 使用モデル

    Returns:
        イベントデータ
    """
    return format_system_message_event(
        subtype="init",
        data={
            "session_id": session_id,
            "tools": tools,
            "model": model,
        },
    )


def format_text_delta_event(text: str) -> dict:
    """
    テキスト増分イベントをフォーマット（messages形式）

    Args:
        text: テキスト増分

    Returns:
        イベントデータ
    """
    return format_assistant_message_event(
        content_blocks=[{"type": "text", "text": text}]
    )


def format_tool_start_event(
    tool_use_id: str,
    tool_name: str,
    summary: str,
    tool_input: dict | None = None,
) -> dict:
    """
    ツール開始イベントをフォーマット（messages形式）

    Args:
        tool_use_id: ツール使用ID
        tool_name: ツール名
        summary: サマリー
        tool_input: ツール入力パラメータ

    Returns:
        イベントデータ
    """
    input_data = tool_input or {}
    # 大きな入力値は切り詰める
    if tool_input:
        input_data = {}
        for key, value in tool_input.items():
            if isinstance(value, str) and len(value) > 500:
                input_data[key] = value[:500] + "..."
            else:
                input_data[key] = value

    return format_assistant_message_event(
        content_blocks=[{
            "type": "tool_use",
            "id": tool_use_id,
            "name": tool_name,
            "input": input_data,
            "summary": summary,
        }]
    )


def format_tool_complete_event(
    tool_use_id: str,
    tool_name: str,
    status: str,
    summary: str,
    result_preview: str | None = None,
    is_error: bool = False,
) -> dict:
    """
    ツール完了イベントをフォーマット（messages形式）

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
    return format_user_result_message_event(
        content_blocks=[{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "content": result_preview or summary,
            "is_error": is_error,
            "status": status,
        }]
    )


def format_thinking_event(content: str) -> dict:
    """
    思考プロセスイベントをフォーマット（messages形式）

    Args:
        content: 思考内容

    Returns:
        イベントデータ
    """
    return format_assistant_message_event(
        content_blocks=[{"type": "thinking", "text": content}]
    )


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
) -> dict:
    """
    結果イベントをフォーマット（messages形式）

    Args:
        subtype: サブタイプ (success / error_during_execution)
        result: 結果テキスト
        errors: エラーリスト
        usage: 使用状況
        cost_usd: コスト（USD）
        num_turns: ターン数
        duration_ms: 実行時間（ミリ秒）
        tools_summary: ツール使用サマリー（互換性のため残すが、messages形式では使用しない）
        session_id: セッションID

    Returns:
        イベントデータ
    """
    return format_result_message_event(
        subtype=subtype,
        result=result,
        errors=errors,
        usage=usage,
        cost_usd=cost_usd,
        num_turns=num_turns,
        duration_ms=duration_ms,
        session_id=session_id,
    )


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
            "timestamp": _get_timestamp(),
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
            "timestamp": _get_timestamp(),
        },
    }
