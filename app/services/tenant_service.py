"""
テナントサービス
テナントの管理
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.repositories.tenant_repository import TenantRepository


class TenantService:
    """テナントサービスクラス"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TenantRepository(db)

    async def get_by_id(self, tenant_id: str) -> Tenant | None:
        """テナントIDで取得"""
        return await self.repo.get_by_id(tenant_id)

    async def get_all(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Tenant]:
        """テナント一覧を取得"""
        return await self.repo.get_all_tenants(
            status=status, limit=limit, offset=offset
        )

    async def create(
        self,
        tenant_id: str,
        system_prompt: str | None = None,
        model_id: str | None = None,
    ) -> Tenant:
        """テナントを作成"""
        tenant = Tenant(
            tenant_id=tenant_id,
            system_prompt=system_prompt,
            model_id=model_id,
            status="active",
        )
        return await self.repo.create(tenant)

    async def update(
        self,
        tenant_id: str,
        system_prompt: str | None = None,
        model_id: str | None = None,
        status: str | None = None,
    ) -> Tenant | None:
        """テナントを更新"""
        tenant = await self.repo.get_by_id(tenant_id)
        if not tenant:
            return None

        if system_prompt is not None:
            tenant.system_prompt = system_prompt
        if model_id is not None:
            tenant.model_id = model_id
        if status is not None:
            tenant.status = status

        await self.db.flush()
        await self.db.refresh(tenant)
        return tenant

    async def delete(self, tenant_id: str) -> bool:
        """テナントを削除"""
        return await self.repo.delete(tenant_id)
