"""
SSEストリーミングユーティリティ v2
Server-Sent Events形式でのストリーミング送信

イベント形式:
- init: セッション初期化
- thinking: Extended Thinking
- assistant: テキスト・ツール使用
- tool_call: ツール呼び出し開始
- tool_result: ツール実行結果
- subagent_start: サブエージェント開始
- subagent_end: サブエージェント終了
- progress: 進捗更新（状態・ターン・ツール統合）
- title: タイトル生成
- ping: ハートビート
- done: 完了
- error: エラー

全てのイベントにシーケンス番号（seq）を付与し、順序保証を提供
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sse_starlette.sse import ServerSentEvent


# =============================================================================
# シーケンス番号管理
# =============================================================================


@dataclass
class SequenceCounter:
    """シーケンス番号カウンター"""

    _counter: int = field(default=0, init=False)

    def next(self) -> int:
        """次のシーケンス番号を取得"""
        self._counter += 1
        return self._counter

    @property
    def current(self) -> int:
        """現在のシーケンス番号"""
        return self._counter

    def reset(self) -> None:
        """カウンターをリセット"""
        self._counter = 0


# =============================================================================
# 基本ユーティリティ
# =============================================================================


def _get_timestamp() -> str:
    """現在のタイムスタンプをISO形式で取得"""
    return datetime.utcnow().isoformat() + "Z"


def _create_event(event_type: str, seq: int, data: dict[str, Any]) -> dict:
    """
    イベントを生成

    Args:
        event_type: イベントタイプ
        seq: シーケンス番号
        data: イベントデータ

    Returns:
        イベント辞書
    """
    return {
        "event": event_type,
        "data": {
            "seq": seq,
            "timestamp": _get_timestamp(),
            **data,
        },
    }


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


# =============================================================================
# イベント生成関数
# =============================================================================


def format_init_event(
    seq: int,
    session_id: str,
    tools: list[str],
    model: str,
    conversation_id: str | None = None,
    max_turns: int | None = None,
) -> dict:
    """
    初期化イベントをフォーマット

    Args:
        seq: シーケンス番号
        session_id: セッションID
        tools: 利用可能なツールリスト
        model: 使用モデル
        conversation_id: 会話ID
        max_turns: 最大ターン数

    Returns:
        イベントデータ
    """
    data = {
        "session_id": session_id,
        "tools": tools,
        "model": model,
    }
    if conversation_id:
        data["conversation_id"] = conversation_id
    if max_turns is not None:
        data["max_turns"] = max_turns

    return _create_event("init", seq, data)


def format_thinking_event(
    seq: int,
    content: str,
    parent_agent_id: str | None = None,
) -> dict:
    """
    思考プロセスイベントをフォーマット

    Args:
        seq: シーケンス番号
        content: 思考内容
        parent_agent_id: 親エージェントID（サブエージェント内の場合）

    Returns:
        イベントデータ
    """
    data = {
        "content": content,
    }
    if parent_agent_id:
        data["parent_agent_id"] = parent_agent_id

    return _create_event("thinking", seq, data)


def format_assistant_event(
    seq: int,
    content_blocks: list[dict[str, Any]],
    parent_agent_id: str | None = None,
) -> dict:
    """
    アシスタントメッセージイベントをフォーマット

    Args:
        seq: シーケンス番号
        content_blocks: コンテンツブロックのリスト
        parent_agent_id: 親エージェントID（サブエージェント内の場合）

    Returns:
        イベントデータ
    """
    data = {
        "content_blocks": content_blocks,
    }
    if parent_agent_id:
        data["parent_agent_id"] = parent_agent_id

    return _create_event("assistant", seq, data)


def format_tool_call_event(
    seq: int,
    tool_use_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    summary: str,
    parent_agent_id: str | None = None,
) -> dict:
    """
    ツール呼び出しイベントをフォーマット

    Args:
        seq: シーケンス番号
        tool_use_id: ツール使用ID
        tool_name: ツール名
        tool_input: ツール入力パラメータ（切り詰め済み）
        summary: サマリー
        parent_agent_id: 親エージェントID（サブエージェント内の場合）

    Returns:
        イベントデータ
    """
    # 大きな入力値は切り詰める
    truncated_input = {}
    for key, value in tool_input.items():
        if isinstance(value, str) and len(value) > 500:
            truncated_input[key] = value[:500] + "..."
        else:
            truncated_input[key] = value

    data = {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "input": truncated_input,
        "summary": summary,
    }
    if parent_agent_id:
        data["parent_agent_id"] = parent_agent_id

    return _create_event("tool_call", seq, data)


def format_tool_result_event(
    seq: int,
    tool_use_id: str,
    tool_name: str,
    status: str,
    content: str,
    is_error: bool = False,
    parent_agent_id: str | None = None,
) -> dict:
    """
    ツール結果イベントをフォーマット

    Args:
        seq: シーケンス番号
        tool_use_id: ツール使用ID
        tool_name: ツール名
        status: ステータス（completed / error）
        content: 結果内容（プレビュー）
        is_error: エラーかどうか
        parent_agent_id: 親エージェントID（サブエージェント内の場合）

    Returns:
        イベントデータ
    """
    data = {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "status": status,
        "content": content,
        "is_error": is_error,
    }
    if parent_agent_id:
        data["parent_agent_id"] = parent_agent_id

    return _create_event("tool_result", seq, data)


def format_subagent_start_event(
    seq: int,
    agent_id: str,
    agent_type: str,
    description: str,
    model: str | None = None,
) -> dict:
    """
    サブエージェント開始イベントをフォーマット

    Args:
        seq: シーケンス番号
        agent_id: エージェントID（tool_use_id）
        agent_type: エージェントタイプ
        description: 説明
        model: 使用モデル

    Returns:
        イベントデータ
    """
    data = {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "description": description,
    }
    if model:
        data["model"] = model

    return _create_event("subagent_start", seq, data)


def format_subagent_end_event(
    seq: int,
    agent_id: str,
    agent_type: str,
    status: str,
    result_preview: str | None = None,
) -> dict:
    """
    サブエージェント終了イベントをフォーマット

    Args:
        seq: シーケンス番号
        agent_id: エージェントID（tool_use_id）
        agent_type: エージェントタイプ
        status: ステータス（completed / error）
        result_preview: 結果プレビュー

    Returns:
        イベントデータ
    """
    data = {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "status": status,
    }
    if result_preview:
        data["result_preview"] = result_preview

    return _create_event("subagent_end", seq, data)


def format_progress_event(
    seq: int,
    progress_type: str,
    message: str,
    turn: int | None = None,
    max_turns: int | None = None,
    tool_use_id: str | None = None,
    tool_name: str | None = None,
    tool_status: str | None = None,
    parent_agent_id: str | None = None,
) -> dict:
    """
    進捗イベントをフォーマット（統合型）

    Args:
        seq: シーケンス番号
        progress_type: 進捗タイプ（thinking / generating / tool / turn）
        message: 進捗メッセージ
        turn: 現在のターン番号
        max_turns: 最大ターン数
        tool_use_id: ツール使用ID（tool タイプ時）
        tool_name: ツール名（tool タイプ時）
        tool_status: ツールステータス（pending / running / completed / error）
        parent_agent_id: 親エージェントID（サブエージェント内の場合）

    Returns:
        イベントデータ
    """
    data: dict[str, Any] = {
        "type": progress_type,
        "message": message,
    }

    if turn is not None:
        data["turn"] = turn
    if max_turns is not None:
        data["max_turns"] = max_turns
    if tool_use_id:
        data["tool_use_id"] = tool_use_id
    if tool_name:
        data["tool_name"] = tool_name
    if tool_status:
        data["tool_status"] = tool_status
    if parent_agent_id:
        data["parent_agent_id"] = parent_agent_id

    return _create_event("progress", seq, data)


def format_title_event(seq: int, title: str) -> dict:
    """
    タイトル生成イベントをフォーマット

    Args:
        seq: シーケンス番号
        title: 生成されたタイトル

    Returns:
        イベントデータ
    """
    return _create_event("title", seq, {"title": title})


def format_ping_event(seq: int, elapsed_ms: int) -> dict:
    """
    ハートビート（ping）イベントをフォーマット

    Args:
        seq: シーケンス番号
        elapsed_ms: 経過時間（ミリ秒）

    Returns:
        イベントデータ
    """
    return _create_event("ping", seq, {"elapsed_ms": elapsed_ms})


def format_done_event(
    seq: int,
    status: str,
    result: str | None,
    errors: list[str] | None,
    usage: dict[str, Any],
    cost_usd: float | str,
    turn_count: int,
    duration_ms: int,
    session_id: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    model_usage: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """
    完了イベントをフォーマット

    Args:
        seq: シーケンス番号
        status: ステータス（success / error / cancelled）
        result: 結果テキスト
        errors: エラーリスト
        usage: 使用状況
        cost_usd: コスト（USD）
        turn_count: ターン数
        duration_ms: 実行時間（ミリ秒）
        session_id: セッションID
        messages: メッセージログ
        model_usage: モデル別使用量

    Returns:
        イベントデータ
    """
    data: dict[str, Any] = {
        "status": status,
        "result": result,
        "is_error": status != "success",
        "errors": errors,
        "usage": usage,
        "cost_usd": cost_usd,
        "turn_count": turn_count,
        "duration_ms": duration_ms,
    }
    if session_id is not None:
        data["session_id"] = session_id
    if messages is not None:
        data["messages"] = messages
    if model_usage is not None:
        data["model_usage"] = model_usage

    return _create_event("done", seq, data)


def format_error_event(
    seq: int,
    error_type: str,
    message: str,
    recoverable: bool = False,
) -> dict:
    """
    エラーイベントをフォーマット

    Args:
        seq: シーケンス番号
        error_type: エラータイプ
        message: エラーメッセージ
        recoverable: 回復可能かどうか

    Returns:
        イベントデータ
    """
    return _create_event("error", seq, {
        "error_type": error_type,
        "message": message,
        "recoverable": recoverable,
    })


# =============================================================================
# 後方互換性のためのエイリアス関数（非推奨）
# 新しいコードでは上記の関数を直接使用してください
# =============================================================================


def format_heartbeat_event(elapsed_ms: int) -> dict:
    """
    ハートビートイベントをフォーマット（後方互換性用）

    注: seq番号が0になるため、新しいコードでは format_ping_event を使用してください

    Args:
        elapsed_ms: 経過時間（ミリ秒）

    Returns:
        イベントデータ
    """
    return format_ping_event(0, elapsed_ms)


# =============================================================================
# 旧形式からのマイグレーション用ヘルパー
# =============================================================================


def convert_legacy_message_event(legacy_event: dict) -> dict:
    """
    旧形式のmessageイベントを新形式に変換

    Args:
        legacy_event: 旧形式のイベント

    Returns:
        新形式のイベント
    """
    data = legacy_event.get("data", {})
    msg_type = data.get("type")
    subtype = data.get("subtype")

    # シーケンス番号は呼び出し側で管理
    seq = data.get("seq", 0)

    if msg_type == "system" and subtype == "init":
        return format_init_event(
            seq=seq,
            session_id=data.get("data", {}).get("session_id", ""),
            tools=data.get("data", {}).get("tools", []),
            model=data.get("data", {}).get("model", ""),
        )

    elif msg_type == "assistant":
        content_blocks = data.get("content_blocks", [])
        return format_assistant_event(seq=seq, content_blocks=content_blocks)

    elif msg_type == "result":
        return format_done_event(
            seq=seq,
            status=subtype if subtype == "success" else "error",
            result=data.get("result"),
            errors=data.get("errors"),
            usage=data.get("usage", {}),
            cost_usd=data.get("total_cost_usd", 0),
            turn_count=data.get("num_turns", 0),
            duration_ms=data.get("duration_ms", 0),
            session_id=data.get("session_id"),
            messages=data.get("messages"),
            model_usage=data.get("model_usage"),
        )

    # 変換不可能な場合はそのまま返す
    return legacy_event
