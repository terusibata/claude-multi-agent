"""
Claude Agent SDK クライアントラッパー
コンテナ内でSDKを起動し、SSEストリームを生成する

SDK API:
  - query(prompt, options) → AsyncIterator[Message]
  - ClaudeSDKClient(options) → 双方向インタラクティブクライアント
"""
import json
import logging
from collections.abc import AsyncIterator

from workspace_agent.models import ExecuteRequest

logger = logging.getLogger(__name__)


def _build_sdk_options(request: ExecuteRequest) -> dict:
    """SDK実行オプションを組み立てる"""
    options: dict = {
        "model": request.model,
        "cwd": request.cwd,
        "system_prompt": request.system_prompt,
        "max_turns": request.max_iterations,
        "budget_tokens": request.budget_tokens,
    }

    if request.session_id:
        options["session_id"] = request.session_id

    if request.mcp_servers:
        options["mcp_servers"] = [s.model_dump(exclude_none=True) for s in request.mcp_servers]

    return options


async def execute_streaming(request: ExecuteRequest) -> AsyncIterator[str]:
    """
    Claude Agent SDK を実行し、SSEイベント文字列を生成する

    各イベントは 'event: ...\ndata: {...}\n\n' 形式の文字列

    Args:
        request: 実行リクエスト

    Yields:
        SSEイベント文字列
    """
    try:
        from claude_agent_sdk import query
    except ImportError:
        logger.error("claude-agent-sdk がインストールされていません")
        yield _format_sse({"event": "error", "data": {"message": "SDK not available"}})
        return

    options = _build_sdk_options(request)
    logger.info("SDK実行開始: model=%s, cwd=%s", request.model, request.cwd)

    try:
        async for message in query(prompt=request.user_input, options=options):
            # SDKのメッセージオブジェクトを辞書に変換
            msg_data = _message_to_dict(message)
            yield _format_sse(msg_data)

        yield _format_sse({"event": "done", "data": {"status": "completed"}})
    except Exception as e:
        logger.error("SDK実行エラー: %s", str(e), exc_info=True)
        yield _format_sse({"event": "error", "data": {"message": str(e)}})


def _message_to_dict(message) -> dict:
    """SDKメッセージオブジェクトを辞書に変換"""
    # SDKのメッセージ型に応じて変換
    # AssistantMessage, UserMessage, SystemMessage, ResultMessage等
    if hasattr(message, "model_dump"):
        return {"event": "message", "data": message.model_dump()}
    if hasattr(message, "to_dict"):
        return {"event": "message", "data": message.to_dict()}
    if hasattr(message, "__dict__"):
        return {"event": "message", "data": message.__dict__}
    # フォールバック: そのまま文字列化
    return {"event": "message", "data": {"content": str(message)}}


def _format_sse(data: dict) -> str:
    """SSEイベント文字列にフォーマット"""
    event_type = data.get("event", "message")
    payload = data.get("data", data)
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
