"""
テナント管理API
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_active_model
from app.database import get_db
from app.schemas.tenant import TenantCreateRequest, TenantResponse, TenantUpdateRequest
from app.services.tenant_service import TenantService

router = APIRouter(prefix="/tenants")


@router.get(
    "",
    response_model=list[TenantResponse],
    summary="テナント一覧取得",
)
async def list_tenants(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """テナント一覧を取得"""
    service = TenantService(db)
    return await service.get_all(status=status, limit=limit, offset=offset)


@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="テナント作成",
)
async def create_tenant(
    request: TenantCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """テナントを作成"""
    service = TenantService(db)

    # 既存テナントのチェック
    existing = await service.get_by_id(request.tenant_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"テナント '{request.tenant_id}' は既に存在します",
        )

    # モデルの存在確認
    if request.model_id:
        await get_active_model(request.model_id, db)

    tenant = await service.create(
        tenant_id=request.tenant_id,
        system_prompt=request.system_prompt,
        model_id=request.model_id,
    )
    await db.commit()
    return tenant


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="テナント取得",
)
async def get_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
):
    """テナントを取得"""
    service = TenantService(db)
    tenant = await service.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )
    return tenant


@router.put(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="テナント更新",
)
async def update_tenant(
    tenant_id: str,
    request: TenantUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """テナントを更新"""
    service = TenantService(db)

    existing = await service.get_by_id(tenant_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )

    # モデルの存在確認
    if request.model_id:
        await get_active_model(request.model_id, db)

    tenant = await service.update(
        tenant_id=tenant_id,
        system_prompt=request.system_prompt,
        model_id=request.model_id,
        status=request.status,
    )
    await db.commit()
    return tenant


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="テナント削除",
)
async def delete_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
):
    """テナントを削除"""
    service = TenantService(db)
    success = await service.delete(tenant_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"テナント '{tenant_id}' が見つかりません",
        )
    await db.commit()
