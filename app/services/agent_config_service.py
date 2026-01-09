"""
エージェント実行設定サービス
テナントごとのエージェント設定のCRUD操作
"""
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig
from app.schemas.agent_config import AgentConfigCreate, AgentConfigUpdate


class AgentConfigService:
    """エージェント実行設定サービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

    async def get_all_by_tenant(
        self,
        tenant_id: str,
        status: Optional[str] = None,
    ) -> list[AgentConfig]:
        """
        テナントの全エージェント設定を取得

        Args:
            tenant_id: テナントID
            status: フィルタリング用ステータス

        Returns:
            エージェント設定リスト
        """
        query = select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)
        if status:
            query = query.where(AgentConfig.status == status)
        query = query.order_by(AgentConfig.name)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_id(
        self,
        agent_config_id: str,
        tenant_id: str,
    ) -> Optional[AgentConfig]:
        """
        IDでエージェント設定を取得

        Args:
            agent_config_id: エージェント設定ID
            tenant_id: テナントID

        Returns:
            エージェント設定（存在しない場合はNone）
        """
        query = select(AgentConfig).where(
            AgentConfig.agent_config_id == agent_config_id,
            AgentConfig.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create(
        self,
        tenant_id: str,
        config_data: AgentConfigCreate,
    ) -> AgentConfig:
        """
        エージェント設定を作成

        Args:
            tenant_id: テナントID
            config_data: 作成データ

        Returns:
            作成されたエージェント設定
        """
        config = AgentConfig(
            agent_config_id=str(uuid4()),
            tenant_id=tenant_id,
            **config_data.model_dump(),
        )
        self.db.add(config)
        await self.db.flush()
        await self.db.refresh(config)
        return config

    async def update(
        self,
        agent_config_id: str,
        tenant_id: str,
        config_data: AgentConfigUpdate,
    ) -> Optional[AgentConfig]:
        """
        エージェント設定を更新

        Args:
            agent_config_id: エージェント設定ID
            tenant_id: テナントID
            config_data: 更新データ

        Returns:
            更新されたエージェント設定（存在しない場合はNone）
        """
        config = await self.get_by_id(agent_config_id, tenant_id)
        if not config:
            return None

        update_data = config_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(config, field, value)

        await self.db.flush()
        await self.db.refresh(config)
        return config

    async def delete(
        self,
        agent_config_id: str,
        tenant_id: str,
    ) -> bool:
        """
        エージェント設定を削除

        Args:
            agent_config_id: エージェント設定ID
            tenant_id: テナントID

        Returns:
            削除成功かどうか
        """
        config = await self.get_by_id(agent_config_id, tenant_id)
        if not config:
            return False

        await self.db.delete(config)
        return True

    async def get_active_configs(self, tenant_id: str) -> list[AgentConfig]:
        """
        テナントの有効なエージェント設定を取得

        Args:
            tenant_id: テナントID

        Returns:
            有効なエージェント設定リスト
        """
        return await self.get_all_by_tenant(tenant_id, status="active")
