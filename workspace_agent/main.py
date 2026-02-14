"""
ワークスペースエージェント メインアプリケーション
コンテナ内でUnix Domain Socket上のFastAPIとして動作する
"""
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


if __name__ == "__main__":
    import os
    import socket

    sdk_version = "unknown"
    try:
        from claude_agent_sdk import __version__ as _sv
        sdk_version = _sv
    except (ImportError, AttributeError):
        pass

    proxy_sock = "/var/run/ws/proxy.sock"
    proxy_exists = os.path.exists(proxy_sock)

    logger.info(
        "ワークスペースエージェント起動",
        socket=AGENT_SOCKET,
        sdk_version=sdk_version,
        proxy_sock_exists=proxy_exists,
        uid=os.getuid(),
        gid=os.getgid(),
        hostname=socket.gethostname(),
    )
    uvicorn.run(app, uds=AGENT_SOCKET, log_level="info")
