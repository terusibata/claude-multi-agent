"""
会話・履歴API
会話と会話履歴の管理
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.conversation import (
    ConversationResponse,
    MessageLogResponse,
    ConversationUpdateRequest,
)
from app.services.conversation_service import ConversationService

router = APIRouter()


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
    user_id: str = Query(..., description="ユーザーID"),
    agent_config_id: Optional[str] = Query(None, description="エージェント設定ID"),
    title: Optional[str] = Query(None, description="会話タイトル"),
    db: AsyncSession = Depends(get_db),
):
    """
    新しい会話を作成します。
    """
    from uuid import uuid4

    service = ConversationService(db)
    conversation_id = str(uuid4())

    conversation = await service.create_conversation(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        user_id=user_id,
        agent_config_id=agent_config_id,
        title=title,
    )
    return conversation


@router.post(
    "/{conversation_id}/stream",
    summary="会話ストリーミング実行",
)
async def stream_conversation(
    tenant_id: str,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    既存の会話でストリーミング実行を開始します。

    このエンドポイントは /api/tenants/{tenant_id}/execute と同様の機能を提供しますが、
    既存の会話IDを使用します。

    ※ 実際の実装は execute.py の execute_agent を呼び出す形になります。
    """
    # TODO: 既存の会話でストリーミング実行を開始する実装
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="このエンドポイントは未実装です。/api/tenants/{tenant_id}/execute を使用してください。",
    )
