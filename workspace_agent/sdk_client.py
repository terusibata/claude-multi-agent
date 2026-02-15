"""
Claude Agent SDK クライアントラッパー
コンテナ内でSDKを起動し、SSEストリームを生成する

SDK API (claude-agent-sdk == 0.1.36):
  - query(prompt, options) -> AsyncIterator[Message]
  - Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage
"""
import json
import logging
import os
import stat
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from workspace_agent.models import ExecuteRequest

logger = logging.getLogger(__name__)

# CLI stderr ログバッファ（直近のstderr出力を保持し、エラー発生時の診断に使用）
_cli_stderr_lines: list[str] = []
_CLI_STDERR_MAX_LINES = 200


def _build_sdk_options(request: ExecuteRequest):
    """SDK実行オプションを ClaudeAgentOptions として組み立てる"""
    from claude_agent_sdk import ClaudeAgentOptions

    # CLI が必要とするディレクトリを事前作成
    # tmpfs マウントは空なので、CLI が書き込む前にディレクトリ構造を準備
    _ensure_cli_directories()

    # Bedrock 経由の環境変数を明示的に渡す
    # API通信は ANTHROPIC_BEDROCK_BASE_URL で直接ルーティングされるため HTTP_PROXY 不要
    env = {
        "CLAUDE_CODE_USE_BEDROCK": os.environ.get("CLAUDE_CODE_USE_BEDROCK", "1"),
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH": os.environ.get("CLAUDE_CODE_SKIP_BEDROCK_AUTH", "1"),
        "AWS_REGION": os.environ.get("AWS_REGION", "us-west-2"),
        "ANTHROPIC_BEDROCK_BASE_URL": os.environ.get("ANTHROPIC_BEDROCK_BASE_URL", "http://127.0.0.1:8080"),
        # NODE_OPTIONS を明示的にクリア（コンテナ環境変数の継承を防止）
        "NODE_OPTIONS": "",
        # CLI の一時ファイル配置先を明示
        "TMPDIR": "/tmp",
        # サンドボックド bash コマンドの TMPDIR を exec 可能な /workspace に設定
        "CLAUDE_TMPDIR": "/workspace/.tmp",
        # SDK バージョンチェックをスキップ（プリフライトで実施済み）
        "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
    }

    # stderr コールバック: CLI のデバッグ出力をキャプチャ
    # デフォルトでは SDK が stderr を /dev/null に送るため、診断情報が失われる
    def _stderr_callback(line: str):
        global _cli_stderr_lines
        logger.debug("CLI stderr: %s", line)
        _cli_stderr_lines.append(line)
        if len(_cli_stderr_lines) > _CLI_STDERR_MAX_LINES:
            _cli_stderr_lines = _cli_stderr_lines[-_CLI_STDERR_MAX_LINES:]

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


def _ensure_cli_directories():
    """CLI が必要とするディレクトリを事前作成する

    コンテナの /home/appuser と /workspace は tmpfs で初回は空。
    CLI は ~/.claude/, /tmp/claude-{uid}/, /workspace/.tmp/ に書き込む。
    """
    dirs = [
        Path.home() / ".claude",           # CLI 設定ディレクトリ
        Path("/tmp") / f"claude-{os.getuid()}",  # CLI スクラッチパッド
        Path("/workspace/.tmp"),           # サンドボックド bash 用 TMPDIR
        Path("/workspace/.claude"),        # プロジェクト設定ディレクトリ
    ]
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("ディレクトリ作成失敗: %s (%s)", d, e)


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

    # プリフライト診断: CLI バイナリの状態をログに記録
    _preflight_check()

    options = _build_sdk_options(request)
    logger.info(
        "SDK実行開始: model=%s, cwd=%s, sdk_version=%s",
        request.model, request.cwd, _get_sdk_version(),
    )

    # stderr バッファをクリア（新しいリクエストごとにリセット）
    global _cli_stderr_lines
    _cli_stderr_lines = []

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
        error_msg = str(e)
        # CLI stderr の直近ログを含めてエラー情報を充実させる
        stderr_tail = "\n".join(_cli_stderr_lines[-50:]) if _cli_stderr_lines else "(no stderr captured)"
        logger.error(
            "SDK実行エラー: %s (type=%s)\nCLI stderr (last 50 lines):\n%s",
            error_msg, type(e).__name__, stderr_tail,
            exc_info=True,
        )
        if "timeout" in error_msg.lower() and "initialize" in error_msg.lower():
            yield _format_sse("error", {
                "message": "CLI subprocess failed to initialize. "
                           "Check NODE_OPTIONS and CLI binary availability.",
                "error_type": "cli_init_timeout",
                "details": error_msg,
                "cli_stderr_tail": _cli_stderr_lines[-20:] if _cli_stderr_lines else [],
            })
        else:
            yield _format_sse("error", {
                "message": error_msg,
                "cli_stderr_tail": _cli_stderr_lines[-10:] if _cli_stderr_lines else [],
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


def _preflight_check() -> None:
    """CLI バイナリの存在・権限・実行可否を診断ログに記録する"""
    try:
        import claude_agent_sdk as sdk
        pkg_dir = Path(sdk.__file__).parent
        bundled = pkg_dir / "_bundled" / "claude"

        if not bundled.exists():
            logger.error("CLI バイナリが見つかりません: %s", bundled)
            return

        st = bundled.stat()
        is_exec = bool(st.st_mode & stat.S_IXUSR)
        logger.info(
            "CLI バイナリ診断: path=%s, size=%d, mode=%o, executable=%s",
            bundled, st.st_size, st.st_mode, is_exec,
        )

        if not is_exec:
            logger.error(
                "CLI バイナリに実行権限がありません（chmod +x が必要）: %s", bundled,
            )
            return

        # バージョン確認（タイムアウト5秒）
        result = subprocess.run(
            [str(bundled), "-v"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "NODE_OPTIONS": ""},
        )
        logger.info(
            "CLI バージョン確認: returncode=%d, stdout=%s, stderr=%s",
            result.returncode,
            result.stdout.strip()[:200],
            result.stderr.strip()[:500],
        )

        # socat ブリッジの疎通確認
        _check_socat_bridge()

    except Exception as e:
        logger.error("CLI プリフライトチェック失敗: %s", e, exc_info=True)


def _check_socat_bridge() -> None:
    """socat TCP:8080 → proxy.sock ブリッジの疎通を確認"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(("127.0.0.1", 8080))
            logger.info("socat ブリッジ疎通OK: 127.0.0.1:8080 接続成功")
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        logger.error(
            "socat ブリッジ疎通NG: 127.0.0.1:8080 接続失敗 (%s). "
            "proxy.sock が存在しない可能性あり",
            e,
        )


def _get_sdk_version() -> str:
    """インストール済みSDKバージョンを返す（診断用）"""
    try:
        from importlib.metadata import version
        return version("claude-agent-sdk")
    except Exception:
        return "unknown"


def _format_sse(event_type: str, data: dict) -> str:
    """SSEイベント文字列にフォーマット"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
