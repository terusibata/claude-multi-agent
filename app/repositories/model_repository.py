"""
モデルリポジトリ
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.repositories.base import BaseRepository


class ModelRepository(BaseRepository[Model]):
    """モデルのデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(db, Model, id_field="model_id")

    async def get_active(self, model_id: str) -> Model | None:
        """アクティブなモデルを取得（存在しないか非アクティブならNone）"""
        model = await self.get_by_id(model_id)
        if model and model.status == "active":
            return model
        return None

    async def get_all_models(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Model]:
        """モデル一覧を取得"""
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

    async def get_by_ids(self, model_ids: list[str]) -> dict[str, Model]:
        """複数のモデルIDでモデルを取得（辞書形式で返す）"""
        if not model_ids:
            return {}
        query = select(Model).where(Model.model_id.in_(model_ids))
        result = await self.db.execute(query)
        models = result.scalars().all()
        return {m.model_id: m for m in models}
