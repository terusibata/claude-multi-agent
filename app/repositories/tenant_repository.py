"""
テナントリポジトリ
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    """テナントのデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(db, Tenant, id_field="tenant_id")

    async def get_all_tenants(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Tenant]:
        """テナント一覧を取得"""
        filters = {}
        if status:
            filters["status"] = status
        return await self.get_all(
            limit=limit,
            offset=offset,
            order_by="created_at",
            order_desc=True,
            **filters,
        )
