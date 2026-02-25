"""
ワークスペースエージェント メインアプリケーション
AgentCore Runtime コンテナとして動作する

AgentCore サービスコントラクト:
  - POST /invocations — 完全な呼出パイプライン
  - GET /ping — ヘルスチェック（200返却）
  - GET /health — 後方互換ヘルスチェック

ポート 8080 で HTTP リスン
"""
import asyncio
import base64
import json
import os
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse

from workspace_agent.models import (
    HealthResponse,
    InvocationRequest,
)
from workspace_agent.sdk_client import execute_streaming

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

AGENT_HTTP_PORT = int(os.environ.get("AGENT_HTTP_PORT", "8080"))

app = FastAPI(title="Workspace Agent", docs_url=None, redoc_url=None)


@app.post("/invocations")
async def invocations(request: InvocationRequest) -> StreamingResponse:
    """AgentCore /invocations エンドポイント

    完全な呼出パイプライン:
    1. workspace_sync が有効なら S3からワークスペース復元 + SDKセッションファイル復元
    2. skill_files があればbase64デコードして書き出し
    3. execute_streaming() でSDK実行（SSEストリーム返却）
    4. 実行完了後、S3へワークスペース同期 + セッションファイル保存
    5. file_manifest イベントをSSE末尾に追加
    """
    logger.info("invocationリクエスト受信", model=request.model, cwd=request.cwd)

    async def _pipeline():
        from workspace_agent.agent_file_sync import (
            get_file_manifest,
            restore_from_s3,
            restore_session_file,
            save_session_file,
            sync_to_s3,
        )

        ws = request.workspace_sync
        session_id_from_done = None

        # 1. S3からワークスペース復元
        if ws and ws.enabled and ws.s3_bucket:
            try:
                await restore_from_s3(
                    s3_bucket=ws.s3_bucket,
                    s3_prefix=ws.s3_prefix,
                    tenant_id=ws.tenant_id,
                    conversation_id=ws.conversation_id,
                    region=request.aws_region,
                )
            except Exception as e:
                logger.error("ワークスペース復元エラー（続行）", error=str(e))

            # SDKセッションファイル復元
            if request.session_id:
                try:
                    await restore_session_file(
                        s3_bucket=ws.s3_bucket,
                        s3_prefix=ws.s3_prefix,
                        tenant_id=ws.tenant_id,
                        conversation_id=ws.conversation_id,
                        session_id=request.session_id,
                        region=request.aws_region,
                    )
                except Exception as e:
                    logger.warning("セッションファイル復元エラー（続行）", error=str(e))

        # 2. スキルファイル書き出し
        if request.skill_files:
            _write_skill_files(request.skill_files)

        # 3. SDK実行（SSEストリーム）
        async for event_str in execute_streaming(request):
            yield event_str
            # done イベントから session_id を抽出
            if "event: done\n" in event_str:
                try:
                    data_line = event_str.split("data: ", 1)[1].split("\n")[0]
                    done_data = json.loads(data_line)
                    session_id_from_done = done_data.get("session_id")
                except Exception:
                    pass

        # 4. S3へワークスペース同期 + セッションファイル保存
        if ws and ws.enabled and ws.s3_bucket:
            try:
                await sync_to_s3(
                    s3_bucket=ws.s3_bucket,
                    s3_prefix=ws.s3_prefix,
                    tenant_id=ws.tenant_id,
                    conversation_id=ws.conversation_id,
                    region=request.aws_region,
                )
            except Exception as e:
                logger.error("ワークスペースS3同期エラー（続行）", error=str(e))

            # セッションファイル保存
            sid = session_id_from_done or request.session_id
            if sid:
                try:
                    await save_session_file(
                        s3_bucket=ws.s3_bucket,
                        s3_prefix=ws.s3_prefix,
                        tenant_id=ws.tenant_id,
                        conversation_id=ws.conversation_id,
                        session_id=sid,
                        region=request.aws_region,
                    )
                except Exception as e:
                    logger.warning("セッションファイル保存エラー（続行）", error=str(e))

        # 5. file_manifest イベントを末尾に追加
        manifest = get_file_manifest()
        manifest_event = (
            f"event: file_manifest\n"
            f"data: {json.dumps({'files': manifest}, ensure_ascii=False)}\n\n"
        )
        yield manifest_event

    return StreamingResponse(
        _pipeline(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/ping")
async def ping() -> Response:
    """AgentCore ヘルスチェック（200返却）"""
    return Response(content="ok", media_type="text/plain")


@app.get("/health")
async def health() -> HealthResponse:
    """後方互換ヘルスチェック"""
    return HealthResponse(status="ok")


def _write_skill_files(skill_files: dict[str, str]) -> None:
    """base64エンコードされたスキルファイルをデコードして書き出し

    Args:
        skill_files: {relative_path: base64_content} の辞書
    """
    for relative_path, b64_content in skill_files.items():
        dest = Path("/workspace") / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = base64.b64decode(b64_content)
            dest.write_bytes(data)
            logger.debug("スキルファイル書き出し: %s (%d bytes)", relative_path, len(data))
        except Exception as e:
            logger.error("スキルファイル書き出しエラー: %s: %s", relative_path, e)


if __name__ == "__main__":
    logger.info(
        "ワークスペースエージェント起動（AgentCoreモード）",
        port=AGENT_HTTP_PORT,
    )
    uvicorn.run(app, host="0.0.0.0", port=AGENT_HTTP_PORT, log_level="info")
