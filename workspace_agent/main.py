"""
ワークスペースエージェント メインアプリケーション
コンテナ内でUnix Domain Socket上のFastAPIとして動作する
"""
import logging
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from workspace_agent.models import ExecuteRequest, HealthResponse
from workspace_agent.sdk_client import execute_streaming

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

AGENT_SOCKET = "/var/run/agent.sock"

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
    logger.info("ワークスペースエージェント起動: socket=%s", AGENT_SOCKET)
    uvicorn.run(app, uds=AGENT_SOCKET, log_level="info")
