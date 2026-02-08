"""
会話管理・ストリーミングAPI
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import AsyncIterator, Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.conversation import (
    ConversationArchiveRequest,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageLogResponse,
)
from app.schemas.execute import ExecuteRequest, StreamRequest
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.conversation_service import ConversationService
from app.services.execute_service import ExecuteService
from app.services.tenant_service import TenantService
from app.services.workspace_service import WorkspaceService
from app.utils.streaming import format_error_event, format_ping_event


def _get_orchestrator() -> ContainerOrchestrator:
    """アプリケーション状態からオーケストレーターを取得"""
    from app.main import app
    return app.state.orchestrator

router = APIRouter()
logger = logging.getLogger(__name__)

# タイムアウト関連の定数
EVENT_TIMEOUT_SECONDS = 300  # イベント待機タイムアウト（秒）
MAX_CONSECUTIVE_TIMEOUTS = 3  # 連続タイムアウトの最大回数
HEARTBEAT_INTERVAL_SECONDS = 10  # ハートビート送信間隔（秒）


# =============================================================================
# 会話管理エンドポイント
# =============================================================================


@router.get(
    "",
    response_model=list[ConversationResponse],
    summary="会話一覧取得",
)
async def get_conversations(
    tenant_id: str,
    user_id: Optional[str] = Query(None, description="ユーザーIDフィルター"),
    status_filter: Optional[str] = Query(None, alias="status", description="ステータスフィルター"),
    from_date: Optional[datetime] = Query(None, description="開始日時（タイムゾーンなしの場合JSTとして扱う）"),
    to_date: Optional[datetime] = Query(None, description="終了日時（タイムゾーンなしの場合JSTとして扱う）"),
    limit: int = Query(50, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントの会話一覧を取得します。
    """
    # テナント存在確認
    tenant_service = TenantService(db)
    tenant = await tenant_service.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=404,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )

    service = ConversationService(db)
    return await service.get_conversations_by_tenant(
        tenant_id=tenant_id,
        user_id=user_id,
        status=status_filter,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="会話詳細取得",
)
async def get_conversation(
    tenant_id: str,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    指定した会話の詳細を取得します。
    """
    service = ConversationService(db)
    conversation = await service.get_conversation_by_id(conversation_id, tenant_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会話 '{conversation_id}' が見つかりません",
        )
    return conversation


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="会話作成",
)
async def create_conversation(
    tenant_id: str,
    request: ConversationCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    新しい会話を作成します。

    ## リクエストボディ

    - **user_id**: ユーザーID（必須）
    - **model_id**: モデルID（オプション、省略時はテナントのデフォルト）
    - **workspace_enabled**: ワークスペースを有効にするか（オプション、デフォルトfalse）

    タイトルはストリーミング実行時にAIが自動生成します。
    """
    # テナント存在確認
    tenant_service = TenantService(db)
    tenant = await tenant_service.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )

    # モデルIDの決定（リクエスト > テナントのデフォルト）
    model_id = request.model_id or tenant.model_id
    if not model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model_idが指定されていません。リクエストまたはテナントのデフォルトモデルを設定してください。",
        )

    # モデルの存在・アクティブ確認
    model_query = select(Model).where(Model.model_id == model_id)
    model_result = await db.execute(model_query)
    model = model_result.scalar_one_or_none()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{model_id}' が見つかりません",
        )
    if model.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{model_id}' は現在利用できません",
        )

    # 会話作成
    conversation_id = str(uuid4())
    service = ConversationService(db)
    conversation = await service.create_conversation(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        user_id=request.user_id,
        model_id=model_id,
        workspace_enabled=request.workspace_enabled,
    )

    await db.commit()
    return conversation


@router.put(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="会話更新",
)
async def update_conversation(
    tenant_id: str,
    conversation_id: str,
    request: ConversationUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    会話を更新します（タイトル変更等）。
    """
    service = ConversationService(db)
    conversation = await service.update_conversation(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        title=request.title,
        status=request.status,
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会話 '{conversation_id}' が見つかりません",
        )
    await db.commit()
    return conversation


@router.post(
    "/{conversation_id}/archive",
    response_model=ConversationResponse,
    summary="会話アーカイブ",
)
async def archive_conversation(
    tenant_id: str,
    conversation_id: str,
    request: ConversationArchiveRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    会話をアーカイブします。
    """
    service = ConversationService(db)
    conversation = await service.archive_conversation(conversation_id, tenant_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会話 '{conversation_id}' が見つかりません",
        )
    await db.commit()
    return conversation


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="会話削除",
)
async def delete_conversation(
    tenant_id: str,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    会話を削除します（関連するログも削除）。
    """
    service = ConversationService(db)
    deleted = await service.delete_conversation(conversation_id, tenant_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会話 '{conversation_id}' が見つかりません",
        )
    await db.commit()


# =============================================================================
# メッセージログエンドポイント
# =============================================================================


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageLogResponse],
    summary="完全メッセージ一覧取得",
)
async def get_message_logs(
    tenant_id: str,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    会話の完全なメッセージログを取得します。
    デバッグ・監査用の詳細データです。
    """
    service = ConversationService(db)
    logs = await service.get_message_logs(conversation_id, tenant_id)
    return logs


# =============================================================================
# ストリーミング実行
# =============================================================================


async def _background_execution(
    request: ExecuteRequest,
    tenant: Tenant,
    model: Model,
    event_queue: asyncio.Queue,
    orchestrator: "ContainerOrchestrator",
) -> None:
    """
    バックグラウンドでコンテナ隔離エージェントを実行し、イベントをキューに送信

    リクエストスコープのセッションはレスポンス返却後にクリーンアップされるため、
    独立したDBセッションを使用する。

    Args:
        request: 実行リクエスト
        tenant: テナント
        model: モデル定義
        event_queue: イベントキュー
        orchestrator: コンテナオーケストレーター
    """
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
                f"Background execution error: {e}",
                exc_info=True,
                extra={"conversation_id": request.conversation_id},
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
    orchestrator: "ContainerOrchestrator",
) -> AsyncIterator[dict]:
    """
    SSEイベントジェネレータ（コンテナ隔離版）
    クライアントが切断しても、バックグラウンド処理は継続します。
    定期的にハートビートを送信して接続を維持します。

    Args:
        request: 実行リクエスト
        tenant: テナント
        model: モデル定義
        orchestrator: コンテナオーケストレーター

    Yields:
        SSEイベント
    """
    event_queue: asyncio.Queue = asyncio.Queue()
    start_time = time.time()
    last_event_time = start_time  # 最後に実イベントを受け取った時刻
    last_heartbeat_time = start_time

    background_task = asyncio.create_task(
        _background_execution(
            request,
            tenant,
            model,
            event_queue,
            orchestrator,
        )
    )

    try:
        while True:
            try:
                # ハートビート間隔でタイムアウトを設定
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS,
                )

                if event is None:
                    break

                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"], ensure_ascii=False, default=str),
                }

                # 実イベント受信時刻とハートビート時刻を更新
                current_time = time.time()
                last_event_time = current_time
                last_heartbeat_time = current_time

            except asyncio.TimeoutError:
                current_time = time.time()

                # pingイベント送信
                elapsed_ms = int((current_time - start_time) * 1000)
                ping_event = format_ping_event(0, elapsed_ms)
                yield {
                    "event": ping_event["event"],
                    "data": json.dumps(ping_event["data"], ensure_ascii=False, default=str),
                }
                last_heartbeat_time = current_time
                logger.debug(
                    "Ping sent",
                    extra={
                        "conversation_id": request.conversation_id,
                        "elapsed_ms": elapsed_ms,
                    },
                )

                # バックグラウンドタスクの完了確認
                if background_task.done():
                    try:
                        await background_task
                    except asyncio.CancelledError:
                        logger.info(
                            "Background task was cancelled",
                            extra={"conversation_id": request.conversation_id},
                        )
                    except Exception as task_error:
                        logger.error(
                            f"Background task error: {task_error}",
                            exc_info=True,
                            extra={"conversation_id": request.conversation_id},
                        )
                        # クライアントにエラーを通知
                        error_event = format_error_event(
                            seq=0,
                            error_type="background_task_error",
                            message=f"バックグラウンドタスクエラー: {str(task_error)}",
                            recoverable=False,
                        )
                        yield {
                            "event": error_event["event"],
                            "data": json.dumps(error_event["data"], ensure_ascii=False, default=str),
                        }
                    else:
                        logger.info(
                            "Background task completed during timeout",
                            extra={"conversation_id": request.conversation_id},
                        )
                    break

                # 最後の実イベントからの経過時間でタイムアウト判定
                time_since_last_event = current_time - last_event_time
                if time_since_last_event >= EVENT_TIMEOUT_SECONDS:
                    logger.error(
                        f"Event timeout reached ({time_since_last_event:.1f}s since last event)",
                        extra={"conversation_id": request.conversation_id},
                    )
                    error_event = format_error_event(
                        seq=0,
                        error_type="timeout_error",
                        message="応答タイムアウト: サーバーからの応答がありません",
                        recoverable=True,
                    )
                    yield {
                        "event": error_event["event"],
                        "data": json.dumps(error_event["data"], ensure_ascii=False, default=str),
                    }
                    break

                continue

    except asyncio.CancelledError:
        logger.info(
            f"Client disconnected for conversation {request.conversation_id}, "
            "but background execution continues"
        )
        raise
    except Exception as e:
        logger.error(
            f"Event generator error: {e}",
            exc_info=True,
            extra={"conversation_id": request.conversation_id},
        )
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        raise
    finally:
        if not background_task.done():
            logger.info(
                f"Cancelling background task for conversation {request.conversation_id}"
            )
            background_task.cancel()
            try:
                await background_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass


@router.post(
    "/{conversation_id}/stream",
    summary="会話ストリーミング実行",
)
async def stream_conversation(
    tenant_id: str,
    conversation_id: str,
    request_data: str = Form(..., description="StreamRequestのJSON文字列"),
    files: list[UploadFile] = File(default=[], description="添付ファイル（複数可、オプション）"),
    file_metadata: str = Form(default="[]", description="FileUploadMetadataのJSONリスト（ファイル添付時は必須）"),
    db: AsyncSession = Depends(get_db),
):
    """
    既存の会話でストリーミング実行を開始します（ファイル添付対応）。

    Content-Type: multipart/form-data

    ## リクエストパラメータ

    - **request_data**: StreamRequestのJSON文字列
    - **files**: 添付ファイル（複数可、オプション）
    - **file_metadata**: FileUploadMetadataのJSONリスト（ファイル添付時は必須）

    ## StreamRequest JSON フィールド

    - **user_input**: ユーザー入力（必須）
    - **executor**: 実行者情報（必須）
    - **tokens**: MCPサーバー用認証情報（オプション）
    - **preferred_skills**: 優先使用するスキル名のリスト（オプション）

    ## FileUploadMetadata JSON フィールド

    - **filename**: 保存用ファイル名（識別子付き）例: route_abcd.ts
    - **original_name**: 元のファイル名 例: route.ts
    - **relative_path**: 保存用の相対パス（識別子付き）例: api/users/route_abcd.ts
    - **original_relative_path**: 元の相対パス（表示用）例: api/users/route.ts
    - **content_type**: MIMEタイプ
    - **size**: ファイルサイズ（バイト）

    ## レスポンス

    Server-Sent Events (SSE) 形式でストリーミング送信されます。
    """
    # テナント取得・検証
    tenant_service = TenantService(db)
    tenant = await tenant_service.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )
    if tenant.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"テナント '{tenant_id}' は現在利用できません",
        )

    # 会話の存在確認
    conversation_service = ConversationService(db)
    conversation = await conversation_service.get_conversation_by_id(conversation_id, tenant_id)
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

    # モデル定義の取得
    model_query = select(Model).where(Model.model_id == conversation.model_id)
    model_result = await db.execute(model_query)
    model = model_result.scalar_one_or_none()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"モデル '{conversation.model_id}' が見つかりません",
        )
    if model.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{conversation.model_id}' は非推奨です",
        )

    # ファイルがある場合はワークスペースにアップロード
    if files and conversation.workspace_enabled:
        # メタデータをパース
        from app.schemas.workspace import FileUploadMetadata

        try:
            metadata_list_raw = json.loads(file_metadata)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"file_metadataのパースに失敗しました: {str(e)}",
            )

        # ファイル数とメタデータ数の整合性チェック
        if len(files) != len(metadata_list_raw):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"ファイル数（{len(files)}）とメタデータ数（{len(metadata_list_raw)}）が一致しません",
            )

        # メタデータをバリデーション
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
            await db.commit()
        except HTTPException:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            logger.error(
                "ファイルアップロードエラー",
                extra={
                    "error": str(e),
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"ファイルのアップロードに失敗しました: {str(e)}",
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

    # オーケストレーターを取得
    orchestrator = _get_orchestrator()

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
