"""
Claude Agent SDK クライアントラッパー
コンテナ内でSDKを起動し、SSEストリームを生成する
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

    各イベントは 'data: {...}\\n\\n' 形式の文字列

    Args:
        request: 実行リクエスト

    Yields:
        SSEイベント文字列
    """
    try:
        from claude_agent_sdk import ClaudeSDKClient
    except ImportError:
        logger.error("claude-agent-sdk がインストールされていません")
        yield _format_sse({"event": "error", "data": {"message": "SDK not available"}})
        return

    options = _build_sdk_options(request)
    logger.info("SDK実行開始: model=%s, cwd=%s", request.model, request.cwd)

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(request.user_input)
            async for message in client.receive_response():
                yield _format_sse(message)

        yield _format_sse({"event": "done", "data": {"status": "completed"}})
    except Exception as e:
        logger.error("SDK実行エラー: %s", str(e), exc_info=True)
        yield _format_sse({"event": "error", "data": {"message": str(e)}})


def _format_sse(data: dict) -> str:
    """SSEイベント文字列にフォーマット"""
    event_type = data.get("event", "message")
    payload = data.get("data", data)
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
