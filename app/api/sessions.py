"""
セッション・履歴API
チャットセッションと会話履歴の管理
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.session import (
    ChatSessionResponse,
    DisplayCacheResponse,
    MessageLogResponse,
    SessionUpdateRequest,
)
from app.services.session_service import SessionService

router = APIRouter()


@router.get("", response_model=list[ChatSessionResponse], summary="セッション一覧取得")
async def get_sessions(
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
    テナントのセッション一覧を取得します。
    """
    service = SessionService(db)
    return await service.get_sessions_by_tenant(
        tenant_id=tenant_id,
        user_id=user_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{session_id}",
    response_model=ChatSessionResponse,
    summary="セッション詳細取得",
)
async def get_session(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    指定したセッションの詳細を取得します。
    """
    service = SessionService(db)
    session = await service.get_session_by_id(session_id, tenant_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"セッション '{session_id}' が見つかりません",
        )
    return session


@router.put(
    "/{session_id}",
    response_model=ChatSessionResponse,
    summary="セッション更新",
)
async def update_session(
    tenant_id: str,
    session_id: str,
    request: SessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    セッションを更新します（タイトル変更等）。
    """
    service = SessionService(db)
    session = await service.update_session(
        chat_session_id=session_id,
        tenant_id=tenant_id,
        title=request.title,
        status=request.status,
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"セッション '{session_id}' が見つかりません",
        )
    return session


@router.post(
    "/{session_id}/archive",
    response_model=ChatSessionResponse,
    summary="セッションアーカイブ",
)
async def archive_session(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    セッションをアーカイブします。
    """
    service = SessionService(db)
    session = await service.archive_session(session_id, tenant_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"セッション '{session_id}' が見つかりません",
        )
    return session


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="セッション削除",
)
async def delete_session(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    セッションを削除します（関連するログも削除）。
    """
    service = SessionService(db)
    deleted = await service.delete_session(session_id, tenant_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"セッション '{session_id}' が見つかりません",
        )


@router.get(
    "/{session_id}/display",
    response_model=list[DisplayCacheResponse],
    summary="表示用キャッシュ取得",
)
async def get_display_cache(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    セッションの表示用キャッシュを取得します。
    UI表示用に最適化されたデータです。
    """
    service = SessionService(db)
    cache = await service.get_display_cache(session_id, tenant_id)
    return cache


@router.get(
    "/{session_id}/messages",
    response_model=list[MessageLogResponse],
    summary="完全メッセージ一覧取得",
)
async def get_message_logs(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    セッションの完全なメッセージログを取得します。
    デバッグ・監査用の詳細データです。
    """
    service = SessionService(db)
    logs = await service.get_message_logs(session_id, tenant_id)
    return logs
