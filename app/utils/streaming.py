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
- context_status: コンテキスト使用状況（警告レベル・継続可否）
- done: 完了
- error: エラー

全てのイベントにシーケンス番号（seq）を付与し、順序保証を提供
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def get_timestamp() -> str:
    """現在のタイムスタンプをISO形式で取得"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_event(event_type: str, seq: int, data: dict[str, Any]) -> dict:
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
            "timestamp": get_timestamp(),
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
) -> dict:
    """
    初期化イベントをフォーマット

    Args:
        seq: シーケンス番号
        session_id: セッションID
        tools: 利用可能なツールリスト
        model: 使用モデル
        conversation_id: 会話ID

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

    return create_event("init", seq, data)


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

    return create_event("thinking", seq, data)


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

    return create_event("assistant", seq, data)


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

    return create_event("tool_call", seq, data)


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

    return create_event("tool_result", seq, data)


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

    return create_event("subagent_start", seq, data)


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

    return create_event("subagent_end", seq, data)


def format_progress_event(
    seq: int,
    progress_type: str,
    message: str,
    tool_use_id: str | None = None,
    tool_name: str | None = None,
    tool_status: str | None = None,
    parent_agent_id: str | None = None,
) -> dict:
    """
    進捗イベントをフォーマット（統合型）

    Args:
        seq: シーケンス番号
        progress_type: 進捗タイプ（thinking / generating / tool）
        message: 進捗メッセージ
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

    if tool_use_id:
        data["tool_use_id"] = tool_use_id
    if tool_name:
        data["tool_name"] = tool_name
    if tool_status:
        data["tool_status"] = tool_status
    if parent_agent_id:
        data["parent_agent_id"] = parent_agent_id

    return create_event("progress", seq, data)


def format_title_event(seq: int, title: str) -> dict:
    """
    タイトル生成イベントをフォーマット

    Args:
        seq: シーケンス番号
        title: 生成されたタイトル

    Returns:
        イベントデータ
    """
    return create_event("title", seq, {"title": title})


def format_ping_event(seq: int, elapsed_ms: int) -> dict:
    """
    ハートビート（ping）イベントをフォーマット

    Args:
        seq: シーケンス番号
        elapsed_ms: 経過時間（ミリ秒）

    Returns:
        イベントデータ
    """
    return create_event("ping", seq, {"elapsed_ms": elapsed_ms})


def format_context_status_event(
    seq: int,
    current_context_tokens: int,
    max_context_tokens: int,
    usage_percent: float,
    warning_level: str,
    can_continue: bool,
    message: str | None = None,
    recommended_action: str | None = None,
) -> dict:
    """
    コンテキスト使用状況イベントをフォーマット

    実行完了後、doneイベントの直前に送信される。
    フロントエンドはこのイベントを受信して、ユーザーに警告を表示したり、
    入力欄を無効化したりすることができる。

    Args:
        seq: シーケンス番号
        current_context_tokens: 現在のコンテキストトークン数
        max_context_tokens: モデルのContext Window上限
        usage_percent: 使用率（%）
        warning_level: 警告レベル
            - "normal": 通常（< 70%）
            - "warning": 警告（70-85%）- 新しいチャット推奨
            - "critical": 重大（85-95%）- 次の返信でエラーの可能性
            - "blocked": ブロック（> 95%）- 送信不可
        can_continue: 次のメッセージを送信可能か
        message: ユーザー向けメッセージ（日本語）
        recommended_action: 推奨アクション（"new_chat" など）

    Returns:
        イベントデータ
    """
    data: dict[str, Any] = {
        "current_context_tokens": current_context_tokens,
        "max_context_tokens": max_context_tokens,
        "usage_percent": round(usage_percent, 1),
        "warning_level": warning_level,
        "can_continue": can_continue,
    }

    if message:
        data["message"] = message
    if recommended_action:
        data["recommended_action"] = recommended_action

    return create_event("context_status", seq, data)


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

    return create_event("done", seq, data)


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
    return create_event("error", seq, {
        "error_type": error_type,
        "message": message,
        "recoverable": recoverable,
    })


def to_sse_payload(event: dict) -> dict:
    """
    内部イベント辞書をSSE送信用ペイロードに変換

    Args:
        event: {"event": <type>, "data": {...}} 形式のイベント

    Returns:
        EventSourceResponse用のSSEペイロード辞書
    """
    return {
        "event": event["event"],
        "data": json.dumps(event["data"], ensure_ascii=False, default=str),
    }
