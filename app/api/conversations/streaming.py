"""
会話ストリーミング実行エンドポイント
"""
import asyncio
import json
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies import get_active_tenant, get_model_with_fallback, get_orchestrator
from app.config import get_settings
from app.database import get_db
from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.execute import ExecuteRequest, StreamRequest
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.conversation_service import ConversationService
from app.services.execute_service import ExecuteService
from app.services.workspace_service import WorkspaceService
from app.utils.streaming import format_error_event, format_ping_event, to_sse_payload

router = APIRouter()
logger = structlog.get_logger(__name__)

# ハートビート間隔（秒）
HEARTBEAT_INTERVAL_SECONDS = 10


async def _background_execution(
    request: ExecuteRequest,
    tenant: Tenant,
    model: Model,
    event_queue: asyncio.Queue,
    orchestrator: ContainerOrchestrator,
) -> None:
    """
    バックグラウンドでコンテナ隔離エージェントを実行し、イベントをキューに送信。
    独立したDBセッションを使用する。
    """
    # 循環インポート回避のため遅延インポート
    from app.database import async_session_maker

    async with async_session_maker() as db:
        try:
            execute_service = ExecuteService(db, orchestrator)
            async for event in execute_service.execute_streaming(
                request=request,
                tenant=tenant,
                model=model,
            ):
                await event_queue.put(event)
        except Exception as e:
            logger.error(
                "バックグラウンド実行エラー",
                error=str(e),
                conversation_id=request.conversation_id,
                exc_info=True,
            )
            error_event = format_error_event(
                seq=0,
                error_type="background_execution_error",
                message=f"バックグラウンド実行エラー: {str(e)}",
                recoverable=False,
            )
            await event_queue.put(error_event)
        finally:
            await event_queue.put(None)


async def _event_generator(
    request: ExecuteRequest,
    tenant: Tenant,
    model: Model,
    orchestrator: ContainerOrchestrator,
) -> AsyncIterator[dict]:
    """
    SSEイベントジェネレータ（コンテナ隔離版）

    タイムアウト階層:
      container_execution_timeout (600s) - httpx絶対タイムアウト（先に発火）
        < event_timeout (720s)           - SSEアイドルタイムアウト（安全ネット）
          < Lock TTL (900s)              - 分散ロック自動失効（最終安全ネット）

    クライアント切断 vs アイドルタイムアウトの動作差異:
      - CancelledError（ブラウザ閉じ）→ バックグラウンド継続（結果保存のため）
      - アイドルタイムアウト → バックグラウンドをキャンセル（スタック状態の解放）
    """
    settings = get_settings()
    event_timeout_seconds = settings.event_timeout

    event_queue: asyncio.Queue = asyncio.Queue()
    start_time = time.time()
    last_event_time = start_time

    background_task = asyncio.create_task(
        _background_execution(request, tenant, model, event_queue, orchestrator)
    )

    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS,
                )

                if event is None:
                    break

                yield to_sse_payload(event)

                current_time = time.time()
                last_event_time = current_time

            except asyncio.TimeoutError:
                current_time = time.time()

                # pingイベント送信
                elapsed_ms = int((current_time - start_time) * 1000)
                ping_event = format_ping_event(0, elapsed_ms)
                yield to_sse_payload(ping_event)

                # バックグラウンドタスクの完了確認
                if background_task.done():
                    try:
                        await background_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as task_error:
                        error_event = format_error_event(
                            seq=0,
                            error_type="background_task_error",
                            message=f"バックグラウンドタスクエラー: {str(task_error)}",
                            recoverable=False,
                        )
                        yield to_sse_payload(error_event)
                    break

                # アイドルタイムアウト判定
                # 通常は container_execution_timeout (httpx) が先に発火し、
                # エラーイベントがキュー経由で届くため、ここには到達しない。
                # ここに到達するのは後処理スタック等の異常時のみ。
                time_since_last_event = current_time - last_event_time
                if time_since_last_event >= event_timeout_seconds:
                    logger.error(
                        "SSEアイドルタイムアウト（バックグラウンドタスクをキャンセル）",
                        elapsed_seconds=round(time_since_last_event, 1),
                        event_timeout=event_timeout_seconds,
                        conversation_id=request.conversation_id,
                    )
                    # アイドルタイムアウト: バックグラウンドタスクをキャンセルして
                    # リソース（コンテナ、ロック、DB接続）を即座に解放する。
                    # ※ CancelledError（ブラウザ閉じ）とは異なり、ここでは
                    #   明らかにスタックしているためキャンセルが正しい。
                    background_task.cancel()
                    try:
                        await background_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    error_event = format_error_event(
                        seq=0,
                        error_type="timeout_error",
                        message="応答タイムアウト: サーバーからの応答がありません",
                        recoverable=True,
                    )
                    yield to_sse_payload(error_event)
                    break

                continue

    except asyncio.CancelledError:
        # クライアント切断（ブラウザ閉じ/ネットワーク断）:
        # バックグラウンドタスクはキャンセルせず継続させる。
        # ExecuteService が DB記録・ファイル同期・ロック解放を最後まで完了する。
        logger.info(
            "クライアント切断（バックグラウンド実行は継続）",
            conversation_id=request.conversation_id,
        )
        return
    except Exception as e:
        logger.error(
            "イベントジェネレーターエラー",
            error=str(e),
            conversation_id=request.conversation_id,
            exc_info=True,
        )
        background_task.cancel()
        try:
            await background_task
        except (asyncio.CancelledError, Exception):
            pass
        raise


@router.post(
    "/{conversation_id}/stream",
    summary="会話ストリーミング実行",
)
async def stream_conversation(
    tenant_id: str,
    conversation_id: str,
    request_data: str = Form(..., description="StreamRequestのJSON文字列"),
    files: list[UploadFile] = File(
        default=[], description="添付ファイル（複数可、オプション）"
    ),
    file_metadata: str = Form(
        default="[]", description="FileUploadMetadataのJSONリスト"
    ),
    tenant: Tenant = Depends(get_active_tenant),
    db: AsyncSession = Depends(get_db),
    orchestrator: ContainerOrchestrator = Depends(get_orchestrator),
):
    """
    既存の会話でストリーミング実行を開始します（ファイル添付対応）。

    Content-Type: multipart/form-data
    """
    # 会話の存在確認
    conversation_service = ConversationService(db)
    conversation = await conversation_service.get_conversation_by_id(
        conversation_id, tenant_id
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会話 '{conversation_id}' が見つかりません",
        )
    if conversation.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"会話 '{conversation_id}' はアーカイブされています",
        )

    # リクエストをパース
    try:
        stream_request = StreamRequest.model_validate_json(request_data)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"リクエストデータのパースに失敗しました: {str(e)}",
        )

    # モデル定義の取得（非推奨の場合は自動フォールバック）
    model = await get_model_with_fallback(conversation.model_id, tenant, db)

    # フォールバックが発生した場合、会話のmodel_idを更新
    if model.model_id != conversation.model_id:
        logger.info(
            "会話のモデルを自動更新",
            conversation_id=conversation_id,
            old_model_id=conversation.model_id,
            new_model_id=model.model_id,
        )
        await conversation_service.update_conversation(
            conversation_id, tenant_id, model_id=model.model_id
        )

    # ファイルがある場合はワークスペースにアップロード
    if files and conversation.workspace_enabled:
        await _handle_file_upload(
            files, file_metadata, tenant_id, conversation_id, db
        )

    # ExecuteRequest作成
    execute_request = ExecuteRequest(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        model_id=conversation.model_id,
        workspace_enabled=conversation.workspace_enabled,
        user_input=stream_request.user_input,
        executor=stream_request.executor,
        tokens=stream_request.tokens,
        preferred_skills=stream_request.preferred_skills,
    )

    # SSEレスポンスを返す
    return EventSourceResponse(
        _event_generator(
            request=execute_request,
            tenant=tenant,
            model=model,
            orchestrator=orchestrator,
        ),
        media_type="text/event-stream",
    )


async def _handle_file_upload(
    files: list[UploadFile],
    file_metadata: str,
    tenant_id: str,
    conversation_id: str,
    db: AsyncSession,
) -> None:
    """ファイルアップロードを処理"""
    # ストリーミング以外のパスでは不要なため遅延インポート
    from app.schemas.workspace import FileUploadMetadata

    try:
        metadata_list_raw = json.loads(file_metadata)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"file_metadataのパースに失敗しました: {str(e)}",
        )

    if len(files) != len(metadata_list_raw):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ファイル数（{len(files)}）とメタデータ数（{len(metadata_list_raw)}）が一致しません",
        )

    try:
        metadata_list = [FileUploadMetadata(**m) for m in metadata_list_raw]
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"メタデータのバリデーションに失敗しました: {str(e)}",
        )

    workspace_service = WorkspaceService(db)
    try:
        for file, metadata in zip(files, metadata_list):
            await workspace_service.upload_user_file_with_metadata(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                file=file,
                metadata=metadata,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "ファイルアップロードエラー",
            error=str(e),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ファイルのアップロードに失敗しました: {str(e)}",
        )
