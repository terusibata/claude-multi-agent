"""
モデル定義サービス
モデル定義のCRUD操作
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.schemas.model import ModelCreate, ModelUpdate


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
        """
        model = await self.get_by_id(model_id)
        if not model:
            return False

        # TODO: 紐づきチェック（agent_configs, usage_logs）
        # 紐づきがある場合は削除不可

        await self.db.delete(model)
        return True

    async def get_active_models(self) -> list[Model]:
        """
        有効なモデル定義を取得

        Returns:
            有効なモデル定義リスト
        """
        return await self.get_all(status="active")
