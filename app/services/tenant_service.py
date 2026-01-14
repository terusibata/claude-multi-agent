"""
テナントサービス
テナントの管理
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantService:
    """テナントサービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

    async def get_by_id(self, tenant_id: str) -> Optional[Tenant]:
        """
        テナントIDで取得

        Args:
            tenant_id: テナントID

        Returns:
            テナント（存在しない場合はNone）
        """
        query = select(Tenant).where(Tenant.tenant_id == tenant_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_all(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Tenant]:
        """
        テナント一覧を取得

        Args:
            status: ステータスフィルター
            limit: 取得件数
            offset: オフセット

        Returns:
            テナントリスト
        """
        query = select(Tenant)

        if status:
            query = query.where(Tenant.status == status)

        query = query.order_by(Tenant.created_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create(
        self,
        tenant_id: str,
        system_prompt: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> Tenant:
        """
        テナントを作成

        Args:
            tenant_id: テナントID
            system_prompt: システムプロンプト
            model_id: デフォルトモデルID

        Returns:
            作成されたテナント
        """
        tenant = Tenant(
            tenant_id=tenant_id,
            system_prompt=system_prompt,
            model_id=model_id,
            status="active",
        )
        self.db.add(tenant)
        await self.db.flush()
        await self.db.refresh(tenant)
        return tenant

    async def update(
        self,
        tenant_id: str,
        system_prompt: Optional[str] = None,
        model_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Tenant]:
        """
        テナントを更新

        Args:
            tenant_id: テナントID
            system_prompt: システムプロンプト
            model_id: デフォルトモデルID
            status: ステータス

        Returns:
            更新されたテナント（存在しない場合はNone）
        """
        tenant = await self.get_by_id(tenant_id)
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
        """
        テナントを削除

        Args:
            tenant_id: テナントID

        Returns:
            削除成功かどうか
        """
        tenant = await self.get_by_id(tenant_id)
        if not tenant:
            return False

        await self.db.delete(tenant)
        return True
