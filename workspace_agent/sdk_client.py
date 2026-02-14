"""
Claude Agent SDK クライアントラッパー
コンテナ内でSDKを起動し、SSEストリームを生成する

SDK API (claude-agent-sdk >= 0.1.33):
  - query(prompt, options) -> AsyncIterator[Message]
  - Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage
"""
import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

from workspace_agent.models import ExecuteRequest

logger = logging.getLogger(__name__)

# SDK初期化タイムアウト時のリトライ設定
_MAX_INIT_RETRIES = 3
_INIT_RETRY_BASE_DELAY = 3.0  # seconds

# ログ出力時にマスクする環境変数キー
_SENSITIVE_ENV_KEYS = frozenset({
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "API_KEY",
})

# 診断ログの重複出力を防ぐフラグ
_diagnostics_logged = False


async def _check_proxy_connectivity(
    host: str = "127.0.0.1",
    port: int = 8080,
    timeout: float = 5.0,
) -> bool:
    """
    TCP接続テストでプロキシの到達可能性を検証する。

    SDK初期化前に実行し、socat→proxy.sockチェーンが
    動作していることを確認する。
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        logger.info("プロキシ接続テスト成功: %s:%d", host, port)
        return True
    except asyncio.TimeoutError:
        logger.warning("プロキシ接続テストタイムアウト: %s:%d (%.1fs)", host, port, timeout)
        return False
    except ConnectionRefusedError:
        logger.warning("プロキシ接続拒否: %s:%d", host, port)
        return False
    except OSError as e:
        logger.warning("プロキシ接続エラー: %s:%d - %s", host, port, str(e))
        return False


def _log_diagnostics() -> None:
    """診断情報をログ出力する（初回のみ）"""
    global _diagnostics_logged
    if _diagnostics_logged:
        return
    _diagnostics_logged = True

    sdk_version = "unknown"
    try:
        from claude_agent_sdk import __version__ as _sv
        sdk_version = _sv
    except (ImportError, AttributeError):
        pass

    proxy_sock = "/var/run/ws/proxy.sock"
    proxy_exists = os.path.exists(proxy_sock)
    proxy_mode = ""
    if proxy_exists:
        try:
            stat = os.stat(proxy_sock)
            proxy_mode = oct(stat.st_mode)[-3:]
        except OSError:
            proxy_mode = "stat-failed"

    logger.info(
        "SDK診断情報: sdk_version=%s, proxy_sock_exists=%s, proxy_sock_mode=%s, "
        "uid=%d, gid=%d, HTTP_PROXY=%s, ANTHROPIC_BEDROCK_BASE_URL=%s",
        sdk_version,
        proxy_exists,
        proxy_mode,
        os.getuid(),
        os.getgid(),
        os.environ.get("HTTP_PROXY", "unset"),
        os.environ.get("ANTHROPIC_BEDROCK_BASE_URL", "unset"),
    )


def _build_sdk_options(request: ExecuteRequest):
    """SDK実行オプションを ClaudeAgentOptions として組み立てる"""
    from claude_agent_sdk import ClaudeAgentOptions

    # SDK は {**os.environ, **options.env} でマージするため、
    # ここで指定した値は os.environ の同名キーを上書きする。
    # os.environ から継承される値（PATH, HOME 等）は
    # 明示的に指定しなくても CLI サブプロセスに引き継がれる。
    env = {
        # Bedrock設定
        "CLAUDE_CODE_USE_BEDROCK": os.environ.get("CLAUDE_CODE_USE_BEDROCK", "1"),
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH": os.environ.get("CLAUDE_CODE_SKIP_BEDROCK_AUTH", "1"),
        "AWS_REGION": os.environ.get("AWS_REGION", "us-west-2"),
        "ANTHROPIC_BEDROCK_BASE_URL": os.environ.get(
            "ANTHROPIC_BEDROCK_BASE_URL", "http://127.0.0.1:8080"
        ),
        # Proxy設定
        "HTTP_PROXY": os.environ.get("HTTP_PROXY", "http://127.0.0.1:8080"),
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:8080"),
        "NO_PROXY": os.environ.get("NO_PROXY", "localhost,127.0.0.1"),
        # Node.js global-agent: CLI サブプロセスで fetch() が Proxy を使用するために必須
        "NODE_OPTIONS": os.environ.get("NODE_OPTIONS", "--require global-agent/bootstrap"),
        "GLOBAL_AGENT_HTTP_PROXY": os.environ.get(
            "GLOBAL_AGENT_HTTP_PROXY", "http://127.0.0.1:8080"
        ),
        "GLOBAL_AGENT_HTTPS_PROXY": os.environ.get(
            "GLOBAL_AGENT_HTTPS_PROXY", "http://127.0.0.1:8080"
        ),
        "GLOBAL_AGENT_NO_PROXY": os.environ.get(
            "GLOBAL_AGENT_NO_PROXY", "localhost,127.0.0.1"
        ),
        # NetworkMode:none コンテナでは非必須トラフィック（テレメトリ/更新チェック等）を無効化
        # https://code.claude.com/docs/en/data-usage
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }

    # 診断ログ: SDK サブプロセスに渡す環境変数を出力（センシティブ値はマスク）
    safe_env = {
        k: ("***" if k in _SENSITIVE_ENV_KEYS else v)
        for k, v in env.items()
    }
    logger.info("SDK env (explicit overrides): %s", safe_env)

    options = ClaudeAgentOptions(
        model=request.model or None,
        cwd=request.cwd,
        system_prompt=request.system_prompt or None,
        max_turns=request.max_turns or None,
        permission_mode="bypassPermissions",
        env=env,
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
    初期化タイムアウト（"Control request timeout: initialize"）時は最大3回リトライする。

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

    # 初回のみ診断情報を出力
    _log_diagnostics()

    last_error = None
    for attempt in range(_MAX_INIT_RETRIES + 1):
        if attempt > 0:
            delay = _INIT_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "SDK初期化リトライ: attempt=%d/%d, delay=%.1fs, error=%s",
                attempt + 1, _MAX_INIT_RETRIES + 1, delay, str(last_error),
            )
            yield _format_sse("system", {
                "subtype": "retry",
                "data": {
                    "attempt": attempt + 1,
                    "max_attempts": _MAX_INIT_RETRIES + 1,
                    "reason": "initialization_timeout",
                },
            })
            await asyncio.sleep(delay)

        # プロキシ接続事前チェック
        proxy_ok = await _check_proxy_connectivity()
        if not proxy_ok:
            logger.error(
                "プロキシ接続テスト失敗: attempt=%d/%d - "
                "socat (127.0.0.1:8080) → proxy.sock チェーンが到達不可。"
                "proxy.sockの存在とパーミッションを確認してください。",
                attempt + 1, _MAX_INIT_RETRIES + 1,
            )
            if attempt < _MAX_INIT_RETRIES:
                last_error = Exception(
                    "Proxy pre-flight check failed: 127.0.0.1:8080 unreachable"
                )
                continue  # リトライ
            else:
                yield _format_sse("error", {
                    "message": "Proxy connectivity check failed after all retries. "
                               "The proxy chain (socat -> proxy.sock) is unreachable.",
                })
                return

        options = _build_sdk_options(request)
        logger.info(
            "SDK実行開始: model=%s, cwd=%s, session_id=%s, attempt=%d/%d",
            request.model, request.cwd, request.session_id,
            attempt + 1, _MAX_INIT_RETRIES + 1,
        )

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
            return  # 正常完了

        except Exception as e:
            error_str = str(e)
            if "Control request timeout" in error_str and attempt < _MAX_INIT_RETRIES:
                last_error = e
                logger.warning(
                    "SDK初期化タイムアウト検出: attempt=%d/%d, error=%s",
                    attempt + 1, _MAX_INIT_RETRIES + 1, error_str,
                )
                continue  # リトライ
            else:
                logger.error("SDK実行エラー: %s", error_str, exc_info=True)
                yield _format_sse("error", {"message": error_str})
                return


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
