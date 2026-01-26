"""
使用状況・コストAPI
トークン使用量とコストの監視・レポート
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.usage import (
    CostReportResponse,
    ToolLogResponse,
    UsageLogResponse,
)
from app.services.usage_service import UsageService

router = APIRouter()


@router.get("/usage", response_model=list[UsageLogResponse], summary="使用状況取得")
async def get_usage(
    tenant_id: str,
    user_id: Optional[str] = Query(None, description="ユーザーIDフィルター"),
    from_date: Optional[datetime] = Query(None, description="開始日時（タイムゾーンなしの場合JSTとして扱う）"),
    to_date: Optional[datetime] = Query(None, description="終了日時（タイムゾーンなしの場合JSTとして扱う）"),
    limit: int = Query(100, ge=1, le=1000, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントの使用状況ログを取得します。
    """
    service = UsageService(db)
    return await service.get_usage_logs(
        tenant_id=tenant_id,
        user_id=user_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get("/usage/users/{user_id}", response_model=list[UsageLogResponse], summary="ユーザー使用状況取得")
async def get_user_usage(
    tenant_id: str,
    user_id: str,
    from_date: Optional[datetime] = Query(None, description="開始日時（タイムゾーンなしの場合JSTとして扱う）"),
    to_date: Optional[datetime] = Query(None, description="終了日時（タイムゾーンなしの場合JSTとして扱う）"),
    limit: int = Query(100, ge=1, le=1000, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db),
):
    """
    特定ユーザーの使用状況ログを取得します。
    """
    service = UsageService(db)
    return await service.get_usage_logs(
        tenant_id=tenant_id,
        user_id=user_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get("/usage/summary", summary="使用状況サマリー取得")
async def get_usage_summary(
    tenant_id: str,
    from_date: Optional[datetime] = Query(None, description="開始日時（タイムゾーンなしの場合JSTとして扱う）"),
    to_date: Optional[datetime] = Query(None, description="終了日時（タイムゾーンなしの場合JSTとして扱う）"),
    group_by: str = Query("day", pattern="^(day|week|month)$", description="グループ化単位"),
    db: AsyncSession = Depends(get_db),
):
    """
    使用状況のサマリーを取得します。

    - **group_by**: day / week / month でグループ化
    """
    service = UsageService(db)
    return await service.get_usage_summary(
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
        group_by=group_by,
    )


@router.get("/cost-report", response_model=CostReportResponse, summary="コストレポート取得")
async def get_cost_report(
    tenant_id: str,
    from_date: datetime = Query(..., description="開始日時（タイムゾーンなしの場合JSTとして扱う）"),
    to_date: datetime = Query(..., description="終了日時（タイムゾーンなしの場合JSTとして扱う）"),
    model_id: Optional[str] = Query(None, description="モデルIDフィルター"),
    user_id: Optional[str] = Query(None, description="ユーザーIDフィルター"),
    db: AsyncSession = Depends(get_db),
):
    """
    コストレポートを生成します。

    モデル別・ユーザー別のコスト内訳を確認できます。
    """
    service = UsageService(db)
    return await service.get_cost_report(
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
        model_id=model_id,
        user_id=user_id,
    )


@router.get("/tool-logs", response_model=list[ToolLogResponse], summary="ツール実行ログ取得")
async def get_tool_logs(
    tenant_id: str,
    session_id: Optional[str] = Query(None, description="セッションIDフィルター"),
    tool_name: Optional[str] = Query(None, description="ツール名フィルター"),
    from_date: Optional[datetime] = Query(None, description="開始日時（タイムゾーンなしの場合JSTとして扱う）"),
    to_date: Optional[datetime] = Query(None, description="終了日時（タイムゾーンなしの場合JSTとして扱う）"),
    limit: int = Query(100, ge=1, le=1000, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db),
):
    """
    ツール実行ログを取得します。

    MCPツールを含むすべてのツール実行の詳細を確認できます。
    """
    service = UsageService(db)
    return await service.get_tool_logs(
        tenant_id=tenant_id,
        session_id=session_id,
        tool_name=tool_name,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
