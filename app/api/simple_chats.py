"""
シンプルチャットAPI
SDKを使わない直接Bedrock呼び出しによるチャットエンドポイント
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.models.model import Model
from app.schemas.simple_chat import (
    SimpleChatDetailResponse,
    SimpleChatListResponse,
    SimpleChatMessageResponse,
    SimpleChatResponse,
    SimpleChatStreamRequest,
)
from app.services.simple_chat_service import SimpleChatService
from app.services.tenant_service import TenantService

router = APIRouter()
logger = logging.getLogger(__name__)


# =============================================================================
# チャット管理エンドポイント
# =============================================================================


@router.get(
    "",
    response_model=SimpleChatListResponse,
    summary="シンプルチャット一覧取得",
)
async def get_simple_chats(
    tenant_id: str,
    user_id: Optional[str] = Query(None, description="ユーザーIDフィルター"),
    application_type: Optional[str] = Query(None, description="アプリケーションタイプフィルター"),
    chat_status: Optional[str] = Query(None, alias="status", description="ステータスフィルター"),
    limit: int = Query(50, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントのシンプルチャット一覧を取得します。
    """
    # テナント存在確認
    tenant_service = TenantService(db)
    tenant = await tenant_service.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )

    service = SimpleChatService(db)
    chats, total = await service.get_chats_by_tenant(
        tenant_id=tenant_id,
        user_id=user_id,
        application_type=application_type,
        status=chat_status,
        limit=limit,
        offset=offset,
    )

    return SimpleChatListResponse(
        items=[SimpleChatResponse.model_validate(chat) for chat in chats],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{chat_id}",
    response_model=SimpleChatDetailResponse,
    summary="シンプルチャット詳細取得",
)
async def get_simple_chat(
    tenant_id: str,
    chat_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    指定したシンプルチャットの詳細（メッセージ履歴含む）を取得します。
    """
    service = SimpleChatService(db)
    chat = await service.get_chat_by_id(chat_id, tenant_id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"チャット '{chat_id}' が見つかりません",
        )

    messages = await service.get_messages(chat_id)

    return SimpleChatDetailResponse(
        chat_id=chat.chat_id,
        tenant_id=chat.tenant_id,
        user_id=chat.user_id,
        model_id=chat.model_id,
        application_type=chat.application_type,
        system_prompt=chat.system_prompt,
        title=chat.title,
        status=chat.status,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        messages=[SimpleChatMessageResponse.model_validate(msg) for msg in messages],
    )


@router.post(
    "/{chat_id}/archive",
    response_model=SimpleChatResponse,
    summary="シンプルチャットアーカイブ",
)
async def archive_simple_chat(
    tenant_id: str,
    chat_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    シンプルチャットをアーカイブします。

    アーカイブされたチャットは継続メッセージを送信できなくなりますが、
    履歴の参照は引き続き可能です。
    """
    service = SimpleChatService(db)
    chat = await service.archive_chat(chat_id, tenant_id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"チャット '{chat_id}' が見つかりません",
        )
    await db.commit()
    return SimpleChatResponse.model_validate(chat)


@router.delete(
    "/{chat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="シンプルチャット削除",
)
async def delete_simple_chat(
    tenant_id: str,
    chat_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    シンプルチャットを削除します（関連するメッセージも削除）。
    """
    service = SimpleChatService(db)
    deleted = await service.delete_chat(chat_id, tenant_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"チャット '{chat_id}' が見つかりません",
        )
    await db.commit()


# =============================================================================
# ストリーミング実行エンドポイント
# =============================================================================


async def _simple_chat_event_generator(
    service: SimpleChatService,
    chat,
    model: Model,
    user_message: str,
):
    """
    シンプルチャットのSSEイベントジェネレータ

    Args:
        service: シンプルチャットサービス
        chat: チャット
        model: モデル定義
        user_message: ユーザーメッセージ

    Yields:
        SSEイベント
    """
    try:
        async for event in service.stream_message(chat, model, user_message):
            yield {
                "event": event["event_type"],
                "data": json.dumps(event, ensure_ascii=False, default=str),
            }
    except Exception as e:
        logger.error(
            f"Simple chat streaming error: {e}",
            exc_info=True,
            extra={"chat_id": chat.chat_id},
        )
        yield {
            "event": "error",
            "data": json.dumps({
                "seq": 0,
                "event_type": "error",
                "message": str(e),
                "error_type": type(e).__name__,
                "recoverable": False,
            }, ensure_ascii=False),
        }


@router.post(
    "/stream",
    summary="シンプルチャットストリーミング実行",
)
async def stream_simple_chat(
    tenant_id: str,
    request: SimpleChatStreamRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    シンプルチャットのストリーミング実行を行います。

    ## 動作モード

    - **新規作成**: `chat_id` を指定しない場合、新しいチャットを作成
    - **継続**: `chat_id` を指定した場合、既存のチャットを継続

    ## リクエストボディ

    ### 新規作成時（chat_idなし）
    - **user_id**: ユーザーID（必須）
    - **application_type**: アプリケーションタイプ（必須、例: translationApp）
    - **system_prompt**: システムプロンプト（必須）
    - **model_id**: モデルID（必須）
    - **message**: ユーザーメッセージ（必須）

    ### 継続時（chat_idあり）
    - **chat_id**: チャットID（必須）
    - **message**: ユーザーメッセージ（必須）

    ## レスポンス

    Server-Sent Events (SSE) 形式でストリーミング送信されます。
    新規作成時はレスポンスヘッダー `X-Chat-ID` にチャットIDが含まれます。

    ### イベントタイプ

    - **text_delta**: テキスト増分
    - **done**: 完了（タイトル、使用量、コスト含む）
    - **error**: エラー
    """
    # テナント存在確認
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

    service = SimpleChatService(db)
    response_headers = {}

    if request.chat_id:
        # ========================================
        # 継続モード: 既存チャットを継続
        # ========================================
        chat = await service.get_chat_by_id(request.chat_id, tenant_id)
        if not chat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"チャット '{request.chat_id}' が見つかりません",
            )
        if chat.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"チャット '{request.chat_id}' はアーカイブされています",
            )

        # モデル取得
        model_query = select(Model).where(Model.model_id == chat.model_id)
        model_result = await db.execute(model_query)
        model = model_result.scalar_one_or_none()
        if not model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"モデル '{chat.model_id}' が見つかりません",
            )

    else:
        # ========================================
        # 新規作成モード: 新しいチャットを作成
        # ========================================

        # 必須パラメータのバリデーション
        if not request.user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="新規作成時は user_id が必須です",
            )
        if not request.application_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="新規作成時は application_type が必須です",
            )
        if not request.system_prompt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="新規作成時は system_prompt が必須です",
            )
        if not request.model_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="新規作成時は model_id が必須です",
            )

        # モデル存在確認
        model_query = select(Model).where(Model.model_id == request.model_id)
        model_result = await db.execute(model_query)
        model = model_result.scalar_one_or_none()
        if not model:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"モデル '{request.model_id}' が見つかりません",
            )
        if model.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"モデル '{request.model_id}' は現在利用できません",
            )

        # チャット作成
        chat = await service.create_chat(
            tenant_id=tenant_id,
            user_id=request.user_id,
            model_id=request.model_id,
            application_type=request.application_type,
            system_prompt=request.system_prompt,
        )
        await db.commit()

        # レスポンスヘッダーにチャットIDを含める
        response_headers["X-Chat-ID"] = chat.chat_id

    # SSEレスポンスを返す
    return EventSourceResponse(
        _simple_chat_event_generator(
            service=service,
            chat=chat,
            model=model,
            user_message=request.message,
        ),
        media_type="text/event-stream",
        headers=response_headers if response_headers else None,
    )
