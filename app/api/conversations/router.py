"""
会話CRUD APIエンドポイント
"""
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_active_model, get_conversation_or_404, get_tenant_or_404
from app.database import get_db
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.schemas.conversation import (
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageLogResponse,
)
from app.services.conversation_service import ConversationService
from app.services.message_log_service import MessageLogService
from app.utils.error_handler import raise_not_found

router = APIRouter()


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="会話一覧取得",
)
async def get_conversations(
    tenant_id: str,
    user_id: str | None = Query(None, description="ユーザーIDフィルター"),
    status_filter: str | None = Query(
        None, alias="status", description="ステータスフィルター"
    ),
    from_date: datetime | None = Query(
        None, description="開始日時（タイムゾーンなしの場合JSTとして扱う）"
    ),
    to_date: datetime | None = Query(
        None, description="終了日時（タイムゾーンなしの場合JSTとして扱う）"
    ),
    limit: int = Query(50, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    tenant: Tenant = Depends(get_tenant_or_404),
    db: AsyncSession = Depends(get_db),
):
    """テナントの会話一覧を取得します。"""
    service = ConversationService(db)
    conversations, total = await service.get_conversations_by_tenant(
        tenant_id=tenant_id,
        user_id=user_id,
        status=status_filter,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )

    return ConversationListResponse(
        items=[ConversationResponse.model_validate(c) for c in conversations],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="会話詳細取得",
)
async def get_conversation(
    conversation: Conversation = Depends(get_conversation_or_404),
):
    """指定した会話の詳細を取得します。"""
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
    tenant: Tenant = Depends(get_tenant_or_404),
    db: AsyncSession = Depends(get_db),
):
    """
    新しい会話を作成します。

    - **user_id**: ユーザーID（必須）
    - **model_id**: モデルID（オプション、省略時はテナントのデフォルト）
    - **workspace_enabled**: ワークスペースを有効にするか（オプション）
    """
    # モデルIDの決定
    model_id = request.model_id or tenant.model_id
    if not model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model_idが指定されていません。リクエストまたはテナントのデフォルトモデルを設定してください。",
        )

    # モデルの存在・アクティブ確認
    model = await get_active_model(model_id, db)

    # 会話作成
    service = ConversationService(db)
    return await service.create_conversation(
        conversation_id=str(uuid4()),
        tenant_id=tenant_id,
        user_id=request.user_id,
        model_id=model_id,
        workspace_enabled=request.workspace_enabled,
    )


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
    """会話を更新します（タイトル変更等）。"""
    service = ConversationService(db)
    conversation = await service.update_conversation(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        title=request.title,
        status=request.status,
    )
    if not conversation:
        raise_not_found("会話", conversation_id)
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
    """会話をアーカイブします。"""
    service = ConversationService(db)
    conversation = await service.archive_conversation(conversation_id, tenant_id)
    if not conversation:
        raise_not_found("会話", conversation_id)
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
    """会話を削除します（関連するログも削除）。"""
    service = ConversationService(db)
    deleted = await service.delete_conversation(conversation_id, tenant_id)
    if not deleted:
        raise_not_found("会話", conversation_id)


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
    """会話の完全なメッセージログを取得します。"""
    service = MessageLogService(db)
    return await service.get_message_logs(conversation_id, tenant_id)
