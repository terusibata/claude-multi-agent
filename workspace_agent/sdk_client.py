"""
Claude Agent SDK クライアントラッパー
コンテナ内でSDKを起動し、SSEストリームを生成する

AgentCore Runtime版: プロキシチェーンを使用せず、実行ロールで直接Bedrockにアクセス

SDK API (claude-agent-sdk >= 0.1.33):
  - query(prompt, options) -> AsyncIterator[Message]
  - Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage

Note: SDK MCP サーバー（create_sdk_mcp_server で作成したカスタムツール）を使用する場合、
prompt にはストリーミング入力モード（async generator）を使用する必要がある。
単純な文字列を渡すと MCP ツールが登録されず "No such tool available" エラーになる。
https://platform.claude.com/docs/en/agent-sdk/custom-tools
"""
import json
import logging
import os
from collections.abc import AsyncIterator

from workspace_agent.models import InvocationRequest

logger = logging.getLogger(__name__)


def _build_sdk_options(request: InvocationRequest):
    """SDK実行オプションを ClaudeAgentOptions として組み立てる"""
    from claude_agent_sdk import ClaudeAgentOptions

    # AgentCore 実行ロールで直接Bedrockにアクセス（プロキシ不要）
    env = {
        "CLAUDE_CODE_USE_BEDROCK": os.environ.get("CLAUDE_CODE_USE_BEDROCK", "1"),
        "AWS_REGION": request.aws_region or os.environ.get("AWS_REGION", "us-west-2"),
        # NODE_OPTIONS を明示的にクリア（CLIバイナリ=standalone ELFが壊れるのを防止）
        "NODE_OPTIONS": "",
        # 基本環境変数
        "HOME": os.environ.get("HOME", "/home/appuser"),
        "TMPDIR": "/tmp",
        "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR", "/home/appuser/.claude"),
        # バージョンチェックスキップ
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

    # ビルトイン MCP サーバー作成（file-tools, file-presentation）
    try:
        from workspace_agent.builtin_mcp import (
            create_builtin_mcp_servers,
            create_openapi_mcp_servers,
        )

        mcp_servers = create_builtin_mcp_servers()

        # OpenAPI MCP サーバー作成（ホストから受け取った設定を使用）
        if request.mcp_server_configs:
            openapi_servers = create_openapi_mcp_servers(request.mcp_server_configs)
            mcp_servers.update(openapi_servers)

        if mcp_servers:
            options.mcp_servers = mcp_servers
            logger.info("MCP servers created: %s", list(mcp_servers.keys()))
    except Exception as e:
        logger.error("MCP server creation failed: %s", str(e))

    if request.allowed_tools:
        options.allowed_tools = request.allowed_tools

    if request.setting_sources:
        options.setting_sources = request.setting_sources

    return options


async def _create_streaming_prompt(user_input: str):
    """
    SDK MCP サーバー用のストリーミング入力プロンプトを生成

    create_sdk_mcp_server で作成したカスタムツールを使用する場合、
    query() の prompt パラメータにはストリーミング入力モード（async generator）
    が必要。単純な文字列では MCP ツールが登録されない。

    See: https://platform.claude.com/docs/en/agent-sdk/custom-tools
    """
    yield {
        "type": "user",
        "message": {
            "role": "user",
            "content": user_input,
        },
    }


async def execute_streaming(request: InvocationRequest) -> AsyncIterator[str]:
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
        yield _format_sse("done", {
            "subtype": "error_during_execution",
            "result": None,
            "session_id": None,
            "num_turns": 0,
            "duration_ms": 0,
            "cost_usd": 0,
            "usage": {},
        })
        return

    options = _build_sdk_options(request)
    has_mcp_servers = hasattr(options, 'mcp_servers') and options.mcp_servers
    logger.info(
        "SDK実行開始: model=%s, cwd=%s, mcp_servers=%s",
        request.model, request.cwd,
        list(options.mcp_servers.keys()) if has_mcp_servers else "none",
    )

    try:
        done_emitted = False
        # メッセージ横断で tool_use_id → tool_name のマッピングを蓄積
        # AssistantMessage内のToolUseBlockで登録し、UserMessage内のToolResultBlockで参照
        tool_name_map: dict[str, str] = {}

        # SDK MCP サーバー（create_sdk_mcp_server）を使用する場合、
        # ストリーミング入力モード（async generator）が必要。
        # 単純な文字列を渡すとカスタム MCP ツールが正しく登録されない。
        # https://platform.claude.com/docs/en/agent-sdk/custom-tools
        if has_mcp_servers:
            prompt_input = _create_streaming_prompt(request.user_input)
        else:
            prompt_input = request.user_input

        async for message in query(prompt=prompt_input, options=options):
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
        logger.error("SDK実行エラー: %s (type=%s)", str(e), type(e).__name__, exc_info=True)
        yield _format_sse("error", {"message": f"{type(e).__name__}: {e}"})
        # エラー後にも done イベントを送信してストリームを正常に終端させる
        yield _format_sse("done", {
            "subtype": "error_during_execution",
            "result": None,
            "session_id": None,
            "num_turns": 0,
            "duration_ms": 0,
            "cost_usd": 0,
            "usage": {},
        })


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
