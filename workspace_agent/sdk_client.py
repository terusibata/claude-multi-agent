"""
Claude Agent SDK クライアントラッパー
コンテナ内でSDKを起動し、SSEストリームを生成する

SDK API (claude-agent-sdk >= 0.1.33):
  - query(prompt, options) -> AsyncIterator[Message]
  - Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage
"""
import json
import logging
import os
from collections.abc import AsyncIterator

from workspace_agent.models import ExecuteRequest

logger = logging.getLogger(__name__)


def _build_sdk_options(request: ExecuteRequest):
    """SDK実行オプションを ClaudeAgentOptions として組み立てる"""
    from claude_agent_sdk import ClaudeAgentOptions

    # Bedrock + Proxy 経由の環境変数を明示的に渡す
    env = {
        "CLAUDE_CODE_USE_BEDROCK": os.environ.get("CLAUDE_CODE_USE_BEDROCK", "1"),
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH": os.environ.get("CLAUDE_CODE_SKIP_BEDROCK_AUTH", "1"),
        "AWS_REGION": os.environ.get("AWS_REGION", "us-west-2"),
        "ANTHROPIC_BEDROCK_BASE_URL": os.environ.get("ANTHROPIC_BEDROCK_BASE_URL", "http://127.0.0.1:8080"),
        "HTTP_PROXY": os.environ.get("HTTP_PROXY", "http://127.0.0.1:8080"),
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:8080"),
        # NO_PROXY: CLIがBedrock Base URLにアクセスする際にProxy経由のループを防止
        "NO_PROXY": os.environ.get("NO_PROXY", "localhost,127.0.0.1"),
        # NODE_OPTIONS を明示的にクリア（CLIバイナリ=standalone ELFが壊れるのを防止）
        "NODE_OPTIONS": "",
        # 基本環境変数
        "HOME": os.environ.get("HOME", "/home/appuser"),
        "TMPDIR": "/tmp",
        "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR", "/home/appuser/.claude"),
        # バージョンチェックスキップ（NetworkMode:none コンテナ用）
        "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
    }

    def _stderr_callback(line: str):
        logger.warning("CLI stderr: %s", line.rstrip())

    options = ClaudeAgentOptions(
        model=request.model or None,
        cwd=request.cwd,
        system_prompt=request.system_prompt or None,
        max_turns=request.max_turns or None,
        permission_mode="bypassPermissions",
        env=env,
        stderr=_stderr_callback,
    )

    # セッション再開: session_id が指定されている場合は resume で既存セッションを継続
    if request.session_id:
        options.resume = request.session_id

    if request.allowed_tools:
        options.allowed_tools = request.allowed_tools

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
        yield _format_sse("error", {"message": "SDK not available"})
        return

    options = _build_sdk_options(request)
    logger.info("SDK実行開始: model=%s, cwd=%s", request.model, request.cwd)

    try:
        done_emitted = False
        # メッセージ横断で tool_use_id → tool_name のマッピングを蓄積
        # AssistantMessage内のToolUseBlockで登録し、UserMessage内のToolResultBlockで参照
        tool_name_map: dict[str, str] = {}
        async for message in query(prompt=request.user_input, options=options):
            sse_events = _message_to_sse_events(message, tool_name_map)
            for event in sse_events:
                if "event: done\n" in event:
                    done_emitted = True
                yield event

        # ResultMessage が来なかった場合のフォールバック
        if not done_emitted:
            yield _format_sse("done", {
                "subtype": "success",
                "result": None,
                "session_id": None,
                "num_turns": 0,
                "duration_ms": 0,
                "cost_usd": 0,
                "usage": {},
            })
    except Exception as e:
        logger.error("SDK実行エラー: %s", str(e), exc_info=True)
        yield _format_sse("error", {"message": str(e)})


def _message_to_sse_events(
    message, tool_name_map: dict[str, str]
) -> list[str]:
    """
    SDKメッセージオブジェクトをSSEイベント文字列のリストに変換

    Args:
        message: SDKメッセージオブジェクト
        tool_name_map: メッセージ横断の tool_use_id → tool_name マッピング。
            AssistantMessage内のToolUseBlockで蓄積し、
            UserMessage内のToolResultBlockで参照する。
    """
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            UserMessage,
        )
        from claude_agent_sdk import TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock
    except ImportError:
        return [_format_sse("message", {"content": str(message)})]

    events = []

    if isinstance(message, AssistantMessage):
        # tool_use_id → tool_name マッピングを蓄積（メッセージ横断で共有）
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tool_name_map[block.id] = block.name

        for block in message.content:
            if isinstance(block, TextBlock):
                events.append(_format_sse("text_delta", {"text": block.text}))
            elif isinstance(block, ToolUseBlock):
                events.append(_format_sse("tool_use", {
                    "tool_use_id": block.id,
                    "tool_name": block.name,
                    "input": block.input,
                }))
            elif isinstance(block, ToolResultBlock):
                events.append(_format_sse("tool_result", {
                    "tool_use_id": block.tool_use_id,
                    "tool_name": tool_name_map.get(block.tool_use_id, ""),
                    "content": str(block.content) if block.content else "",
                    "is_error": block.is_error or False,
                }))
            elif isinstance(block, ThinkingBlock):
                events.append(_format_sse("thinking", {"content": block.thinking}))

    elif isinstance(message, ResultMessage):
        events.append(_format_sse("done", {
            "subtype": "error_during_execution" if message.is_error else "success",
            "result": message.result,
            "session_id": message.session_id,
            "num_turns": message.num_turns,
            "duration_ms": message.duration_ms,
            "cost_usd": message.total_cost_usd,
            "usage": message.usage or {},
        }))

    elif isinstance(message, SystemMessage):
        events.append(_format_sse("system", {
            "subtype": message.subtype,
            "data": message.data,
        }))

    elif isinstance(message, UserMessage):
        # UserMessage 内の ToolResultBlock を処理
        # SDKの実装によっては、ツール実行結果が UserMessage.content 内に
        # ToolResultBlock として含まれる場合がある
        if hasattr(message, "content") and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    # メッセージ横断マップから tool_name を解決
                    # （前の AssistantMessage の ToolUseBlock で登録済み）
                    events.append(_format_sse("tool_result", {
                        "tool_use_id": block.tool_use_id,
                        "tool_name": tool_name_map.get(block.tool_use_id, ""),
                        "content": str(block.content) if block.content else "",
                        "is_error": block.is_error or False,
                    }))

    else:
        # 不明なメッセージ型はスキップ（ログのみ）
        logger.debug("未知のメッセージ型: %s", type(message).__name__)

    return events


def _format_sse(event_type: str, data: dict) -> str:
    """SSEイベント文字列にフォーマット"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
