"""
モデル定義サービス
モデル定義のCRUD操作
"""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.model import Model
from app.models.tenant import Tenant
from app.models.usage_log import UsageLog
from app.schemas.model import ModelCreate, ModelUpdate


class ModelInUseError(Exception):
    """モデルが使用中のため削除できないエラー"""

    def __init__(self, message: str, usage_details: dict):
        self.message = message
        self.usage_details = usage_details
        super().__init__(self.message)


class ModelService:
    """モデル定義サービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

    async def get_all(self, status: Optional[str] = None) -> list[Model]:
        """
        全モデル定義を取得

        Args:
            status: フィルタリング用ステータス

        Returns:
            モデル定義リスト
        """
        query = select(Model)
        if status:
            query = query.where(Model.status == status)
        query = query.order_by(Model.display_name)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_id(self, model_id: str) -> Optional[Model]:
        """
        IDでモデル定義を取得

        Args:
            model_id: モデルID

        Returns:
            モデル定義（存在しない場合はNone）
        """
        query = select(Model).where(Model.model_id == model_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create(self, model_data: ModelCreate) -> Model:
        """
        モデル定義を作成

        Args:
            model_data: 作成データ

        Returns:
            作成されたモデル定義
        """
        model = Model(**model_data.model_dump())
        self.db.add(model)
        await self.db.flush()
        await self.db.refresh(model)
        return model

    async def update(
        self, model_id: str, model_data: ModelUpdate
    ) -> Optional[Model]:
        """
        モデル定義を更新

        Args:
            model_id: モデルID
            model_data: 更新データ

        Returns:
            更新されたモデル定義（存在しない場合はNone）
        """
        model = await self.get_by_id(model_id)
        if not model:
            return None

        update_data = model_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(model, field, value)

        await self.db.flush()
        await self.db.refresh(model)
        return model

    async def update_status(
        self, model_id: str, status: str
    ) -> Optional[Model]:
        """
        モデルステータスを更新

        Args:
            model_id: モデルID
            status: 新しいステータス

        Returns:
            更新されたモデル定義（存在しない場合はNone）
        """
        model = await self.get_by_id(model_id)
        if not model:
            return None

        model.status = status
        await self.db.flush()
        await self.db.refresh(model)
        return model

    async def delete(self, model_id: str) -> bool:
        """
        モデル定義を削除（紐づきがない場合のみ）

        Args:
            model_id: モデルID

        Returns:
            削除成功かどうか

        Raises:
            ModelInUseError: モデルが使用中の場合
        """
        model = await self.get_by_id(model_id)
        if not model:
            return False

        # 紐づきチェック
        usage_details = await self._check_model_usage(model_id)
        if any(usage_details.values()):
            raise ModelInUseError(
                f"モデル '{model_id}' は使用中のため削除できません",
                usage_details,
            )

        await self.db.delete(model)
        return True

    async def _check_model_usage(self, model_id: str) -> dict:
        """
        モデルの使用状況をチェック

        Args:
            model_id: モデルID

        Returns:
            使用状況の詳細（各テーブルでの使用件数）
        """
        # テナントでの使用数
        tenant_query = select(func.count()).where(Tenant.model_id == model_id)
        tenant_result = await self.db.execute(tenant_query)
        tenant_count = tenant_result.scalar() or 0

        # 会話での使用数
        conversation_query = select(func.count()).where(
            Conversation.model_id == model_id
        )
        conversation_result = await self.db.execute(conversation_query)
        conversation_count = conversation_result.scalar() or 0

        # 使用量ログでの使用数
        usage_log_query = select(func.count()).where(UsageLog.model_id == model_id)
        usage_log_result = await self.db.execute(usage_log_query)
        usage_log_count = usage_log_result.scalar() or 0

        return {
            "tenants": tenant_count,
            "conversations": conversation_count,
            "usage_logs": usage_log_count,
        }

    async def get_active_models(self) -> list[Model]:
        """
        有効なモデル定義を取得

        Returns:
            有効なモデル定義リスト
        """
        return await self.get_all(status="active")
