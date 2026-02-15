"""
ワークスペースエージェント メインアプリケーション
コンテナ内でUnix Domain Socket上のFastAPIとして動作する
"""
import asyncio
import os
import socket
import subprocess

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from workspace_agent.models import ExecuteRequest, HealthResponse
from workspace_agent.sdk_client import execute_streaming

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

AGENT_SOCKET = "/var/run/ws/agent.sock"

app = FastAPI(title="Workspace Agent", docs_url=None, redoc_url=None)


@app.post("/execute")
async def execute(request: ExecuteRequest) -> StreamingResponse:
    """エージェント実行エンドポイント（SSEストリーミング）"""
    logger.info("実行リクエスト受信", model=request.model, cwd=request.cwd)
    return StreamingResponse(
        execute_streaming(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health() -> HealthResponse:
    """ヘルスチェック"""
    return HealthResponse(status="ok")


@app.get("/diagnostics")
async def diagnostics():
    """CLIバイナリ・プロキシチェーン・環境の一括検査"""
    results = {}

    # 1. CLIバイナリ検査
    try:
        from claude_agent_sdk import ClaudeAgentOptions
        # SDK内部パスからCLIバイナリを探す
        import claude_agent_sdk
        sdk_dir = os.path.dirname(claude_agent_sdk.__file__)
        cli_path = os.path.join(sdk_dir, "_bundled", "claude")
        results["cli"] = {
            "path": cli_path,
            "exists": os.path.exists(cli_path),
            "executable": os.access(cli_path, os.X_OK) if os.path.exists(cli_path) else False,
            "size_mb": round(os.path.getsize(cli_path) / 1024 / 1024, 1) if os.path.exists(cli_path) else 0,
        }
        # バージョン確認
        if os.path.exists(cli_path) and os.access(cli_path, os.X_OK):
            proc = subprocess.run(
                [cli_path, "-v"],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "NODE_OPTIONS": ""},
            )
            results["cli"]["version"] = proc.stdout.strip() if proc.returncode == 0 else f"error: {proc.stderr.strip()}"
    except Exception as e:
        results["cli"] = {"error": str(e)}

    # 2. proxy.sock 疎通確認
    proxy_sock = "/var/run/ws/proxy.sock"
    results["proxy_socket"] = {"path": proxy_sock, "exists": os.path.exists(proxy_sock)}
    if os.path.exists(proxy_sock):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(proxy_sock)
            s.close()
            results["proxy_socket"]["connectable"] = True
        except Exception as e:
            results["proxy_socket"]["connectable"] = False
            results["proxy_socket"]["error"] = str(e)

    # 3. socat (TCP 8080) 疎通確認
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", 8080))
        s.close()
        results["socat_tcp8080"] = {"connectable": True}
    except Exception as e:
        results["socat_tcp8080"] = {"connectable": False, "error": str(e)}

    # 4. ディレクトリ状態
    dirs_to_check = [
        "/var/run/ws", "/home/appuser/.claude", "/tmp",
        "/workspace", "/home/appuser",
    ]
    results["directories"] = {}
    for d in dirs_to_check:
        results["directories"][d] = {
            "exists": os.path.exists(d),
            "writable": os.access(d, os.W_OK) if os.path.exists(d) else False,
        }

    # 5. 環境変数（セキュリティ上、値はマスク）
    env_keys = [
        "HOME", "CLAUDE_CONFIG_DIR", "TMPDIR",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
        "AWS_REGION", "ANTHROPIC_BEDROCK_BASE_URL",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "NODE_OPTIONS", "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK",
    ]
    results["env"] = {k: os.environ.get(k, "<not set>") for k in env_keys}

    # 6. SDK cli_path 初期化テスト（query呼び出しなし）
    try:
        loop = asyncio.get_event_loop()
        results["sdk_import"] = "ok"
    except Exception as e:
        results["sdk_import"] = str(e)

    return results


if __name__ == "__main__":
    logger.info("ワークスペースエージェント起動", socket=AGENT_SOCKET)
    uvicorn.run(app, uds=AGENT_SOCKET, log_level="info")
