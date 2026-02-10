"""
シンプルチャットCRUD APIエンドポイント
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_simple_chat_or_404, get_tenant_or_404
from app.database import get_db
from app.models.simple_chat import SimpleChat
from app.models.tenant import Tenant
from app.schemas.simple_chat import (
    SimpleChatDetailResponse,
    SimpleChatListResponse,
    SimpleChatMessageResponse,
    SimpleChatResponse,
)
from app.services.simple_chat_service import SimpleChatService
from app.utils.error_handler import raise_not_found

router = APIRouter()


@router.get(
    "",
    response_model=SimpleChatListResponse,
    summary="シンプルチャット一覧取得",
)
async def get_simple_chats(
    tenant_id: str,
    user_id: str | None = Query(None, description="ユーザーIDフィルター"),
    application_type: str | None = Query(
        None, description="アプリケーションタイプフィルター"
    ),
    chat_status: str | None = Query(
        None, alias="status", description="ステータスフィルター"
    ),
    limit: int = Query(50, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    tenant: Tenant = Depends(get_tenant_or_404),
    db: AsyncSession = Depends(get_db),
):
    """テナントのシンプルチャット一覧を取得します。"""
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
    chat: SimpleChat = Depends(get_simple_chat_or_404),
    db: AsyncSession = Depends(get_db),
):
    """指定したシンプルチャットの詳細を取得します。"""
    service = SimpleChatService(db)
    messages = await service.get_messages(chat.chat_id)

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
        messages=[
            SimpleChatMessageResponse.model_validate(msg) for msg in messages
        ],
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
    """シンプルチャットをアーカイブします。"""
    service = SimpleChatService(db)
    chat = await service.archive_chat(chat_id, tenant_id)
    if not chat:
        raise_not_found("チャット", chat_id)
    return SimpleChatResponse.model_validate(chat)


@router.delete(
    "/{chat_id}",
    status_code=204,
    summary="シンプルチャット削除",
)
async def delete_simple_chat(
    tenant_id: str,
    chat_id: str,
    db: AsyncSession = Depends(get_db),
):
    """シンプルチャットを削除します（関連するメッセージも削除）。"""
    service = SimpleChatService(db)
    deleted = await service.delete_chat(chat_id, tenant_id)
    if not deleted:
        raise_not_found("チャット", chat_id)
