"""
API共通依存関係
テナント・モデル検証、オーケストレーター取得などの共通ロジック
"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.model import Model
from app.models.tenant import Tenant
from app.repositories.model_repository import ModelRepository
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.tenant_service import TenantService


def get_orchestrator(request: Request) -> ContainerOrchestrator:
    """アプリケーション状態からオーケストレーターを取得"""
    return request.app.state.orchestrator


async def get_tenant_or_404(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """テナントを取得（存在しない場合は404）"""
    service = TenantService(db)
    tenant = await service.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )
    return tenant


async def get_active_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """アクティブなテナントを取得（存在しないか非アクティブなら例外）"""
    tenant = await get_tenant_or_404(tenant_id, db)
    if tenant.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"テナント '{tenant_id}' は現在利用できません",
        )
    return tenant


async def get_active_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> Model:
    """アクティブなモデルを取得（存在しないか非アクティブなら例外）"""
    repo = ModelRepository(db)
    model = await repo.get_by_id(model_id)
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
    return model
