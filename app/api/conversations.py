"""
会話・履歴API
会話と会話履歴の管理
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.schemas.conversation import (
    ConversationResponse,
    MessageLogResponse,
    ConversationUpdateRequest,
    ConversationCreateRequest,
)
from app.schemas.execute import ExecuteRequest, StreamRequest
from app.services.agent_config_service import AgentConfigService
from app.services.conversation_service import ConversationService
from app.services.execute_service import ExecuteService
from app.services.model_service import ModelService
from app.services.workspace_service import WorkspaceService
from app.utils.streaming import format_error_event

router = APIRouter()
logger = logging.getLogger(__name__)

# タイムアウト関連の定数
EVENT_TIMEOUT_SECONDS = 300  # イベント待機タイムアウト（秒）
MAX_CONSECUTIVE_TIMEOUTS = 3  # 連続タイムアウトの最大回数


@router.get("", response_model=list[ConversationResponse], summary="会話一覧取得")
async def get_conversations(
    tenant_id: str,
    user_id: Optional[str] = Query(None, description="ユーザーIDフィルター"),
    status: Optional[str] = Query(None, description="ステータスフィルター"),
    from_date: Optional[datetime] = Query(None, description="開始日時"),
    to_date: Optional[datetime] = Query(None, description="終了日時"),
    limit: int = Query(50, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントの会話一覧を取得します。
    """
    service = ConversationService(db)
    return await service.get_conversations_by_tenant(
        tenant_id=tenant_id,
        user_id=user_id,
        status=status,
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
    return conversation


@router.post(
    "/{conversation_id}/archive",
    response_model=ConversationResponse,
    summary="会話アーカイブ",
)
async def archive_conversation(
    tenant_id: str,
    conversation_id: str,
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
    - **agent_config_id**: エージェント設定ID（必須）

    タイトルはストリーミング実行時にAIが自動生成します。
    """
    from uuid import uuid4

    service = ConversationService(db)
    conversation_id = str(uuid4())

    conversation = await service.create_conversation(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        user_id=request.user_id,
        agent_config_id=request.agent_config_id,
        title=None,
    )
    return conversation


async def _background_execution(
    execute_service: ExecuteService,
    request: ExecuteRequest,
    agent_config,
    model,
    tenant_id: str,
    event_queue: asyncio.Queue,
) -> None:
    """
    バックグラウンドでエージェントを実行し、イベントをキューに送信

    Args:
        execute_service: 実行サービス
        request: 実行リクエスト
        agent_config: エージェント設定
        model: モデル定義
        tenant_id: テナントID
        event_queue: イベントキュー
    """
    try:
        async for event in execute_service.execute_streaming(
            request=request,
            agent_config=agent_config,
            model=model,
            tenant_id=tenant_id,
        ):
            await event_queue.put(event)
    except Exception as e:
        logger.error(
            f"Background execution error: {e}",
            exc_info=True,
            extra={"conversation_id": request.conversation_id},
        )
        error_event = format_error_event(
            f"バックグラウンド実行エラー: {str(e)}",
            "background_execution_error",
        )
        await event_queue.put(error_event)
    finally:
        await event_queue.put(None)


async def _event_generator(
    execute_service: ExecuteService,
    request: ExecuteRequest,
    agent_config,
    model,
    tenant_id: str,
) -> AsyncIterator[dict]:
    """
    SSEイベントジェネレータ
    クライアントが切断しても、バックグラウンド処理は継続します。

    Args:
        execute_service: 実行サービス
        request: 実行リクエスト
        agent_config: エージェント設定
        model: モデル定義
        tenant_id: テナントID

    Yields:
        SSEイベント
    """
    event_queue: asyncio.Queue = asyncio.Queue()
    consecutive_timeouts = 0

    background_task = asyncio.create_task(
        _background_execution(
            execute_service,
            request,
            agent_config,
            model,
            tenant_id,
            event_queue,
        )
    )

    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=EVENT_TIMEOUT_SECONDS,
                )
                consecutive_timeouts = 0

                if event is None:
                    break

                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"], ensure_ascii=False, default=str),
                }
            except asyncio.TimeoutError:
                consecutive_timeouts += 1
                logger.warning(
                    f"Event queue timeout ({consecutive_timeouts}/{MAX_CONSECUTIVE_TIMEOUTS})",
                    extra={"conversation_id": request.conversation_id},
                )

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
                    else:
                        logger.info(
                            "Background task completed during timeout",
                            extra={"conversation_id": request.conversation_id},
                        )
                    break

                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    logger.error(
                        f"Max consecutive timeouts reached, terminating",
                        extra={"conversation_id": request.conversation_id},
                    )
                    error_event = format_error_event(
                        "応答タイムアウト: サーバーからの応答がありません",
                        "timeout_error",
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
                f"Background task continues for conversation {request.conversation_id}"
            )


@router.post(
    "/{conversation_id}/stream",
    summary="会話ストリーミング実行",
)
async def stream_conversation(
    tenant_id: str,
    conversation_id: str,
    request_data: str = Form(..., description="StreamRequestのJSON文字列"),
    files: list[UploadFile] = File(default=[], description="添付ファイル（複数可、オプション）"),
    db: AsyncSession = Depends(get_db),
):
    """
    既存の会話でストリーミング実行を開始します（ファイル添付対応）。

    Content-Type: multipart/form-data

    ## リクエストパラメータ

    - **request_data**: StreamRequestのJSON文字列
    - **files**: 添付ファイル（複数可、オプション）

    ## StreamRequest JSON フィールド

    - **agent_config_id**: エージェント実行設定ID
    - **model_id**: 使用するモデルID
    - **user_input**: ユーザー入力
    - **executor**: 実行者情報
    - **tokens**: MCPサーバー用認証情報（オプション）
    - **resume_session_id**: 継続するSDKセッションID（オプション）
    - **fork_session**: セッションをフォークするか（オプション）
    - **enable_workspace**: ワークスペースを有効にするか（オプション）

    ## レスポンス

    Server-Sent Events (SSE) 形式でストリーミング送信されます。
    """
    # 会話の存在確認
    conversation_service = ConversationService(db)
    conversation = await conversation_service.get_conversation_by_id(conversation_id, tenant_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会話 '{conversation_id}' が見つかりません",
        )

    # リクエストをパース
    try:
        stream_request = StreamRequest.model_validate_json(request_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"リクエストデータのパースに失敗しました: {str(e)}",
        )

    # StreamRequestをExecuteRequestに変換
    request = stream_request.to_execute_request(conversation_id)

    # ファイルがある場合はS3にアップロード
    if files:
        workspace_service = WorkspaceService(db)
        file_data = []
        try:
            for file in files:
                if not file.filename:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="ファイル名が指定されていません",
                    )
                content = await file.read()
                content_type = file.content_type or "application/octet-stream"
                file_data.append((file.filename, content, content_type))

            await workspace_service.upload_files(tenant_id, conversation_id, file_data)
            request.enable_workspace = True
            await workspace_service.enable_workspace(tenant_id, conversation_id)
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

    # エージェント設定の取得
    config_service = AgentConfigService(db)
    agent_config = await config_service.get_by_id(request.agent_config_id, tenant_id)
    if not agent_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"エージェント設定 '{request.agent_config_id}' が見つかりません",
        )
    if agent_config.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"エージェント設定 '{request.agent_config_id}' は無効です",
        )

    # モデル定義の取得
    model_service = ModelService(db)
    model = await model_service.get_by_id(request.model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"モデル '{request.model_id}' が見つかりません",
        )
    if model.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{request.model_id}' は非推奨です",
        )

    # 実行サービスの作成
    execute_service = ExecuteService(db)

    # SSEレスポンスを返す
    return EventSourceResponse(
        _event_generator(
            execute_service=execute_service,
            request=request,
            agent_config=agent_config,
            model=model,
            tenant_id=tenant_id,
        ),
        media_type="text/event-stream",
    )
