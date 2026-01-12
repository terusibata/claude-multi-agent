"""
ベースリポジトリパターン
共通のデータベース操作を提供
"""
from typing import Any, Generic, Optional, Type, TypeVar
from uuid import uuid4

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):
    """
    ベースリポジトリクラス
    共通のCRUD操作を提供

    使用例:
        class ModelService(BaseRepository[Model]):
            def __init__(self, db: AsyncSession):
                super().__init__(db, Model, "model_id")
    """

    def __init__(
        self,
        db: AsyncSession,
        model: Type[ModelType],
        id_field: str = "id",
        tenant_field: Optional[str] = None,
    ):
        """
        初期化

        Args:
            db: データベースセッション
            model: SQLAlchemyモデルクラス
            id_field: IDフィールド名
            tenant_field: テナントフィールド名（マルチテナント対応時）
        """
        self.db = db
        self.model = model
        self.id_field = id_field
        self.tenant_field = tenant_field

    async def get_by_id(
        self,
        id_value: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[ModelType]:
        """
        IDでエンティティを取得

        Args:
            id_value: ID値
            tenant_id: テナントID（マルチテナント対応時）

        Returns:
            エンティティまたはNone
        """
        id_column = getattr(self.model, self.id_field)
        conditions = [id_column == id_value]

        if self.tenant_field and tenant_id:
            tenant_column = getattr(self.model, self.tenant_field)
            conditions.append(tenant_column == tenant_id)

        query = select(self.model).where(and_(*conditions))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_all(
        self,
        tenant_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> list[ModelType]:
        """
        全エンティティを取得

        Args:
            tenant_id: テナントID
            limit: 取得件数
            offset: オフセット
            order_by: ソートフィールド
            order_desc: 降順フラグ

        Returns:
            エンティティリスト
        """
        conditions = []
        if self.tenant_field and tenant_id:
            tenant_column = getattr(self.model, self.tenant_field)
            conditions.append(tenant_column == tenant_id)

        query = select(self.model)
        if conditions:
            query = query.where(and_(*conditions))

        if order_by:
            order_column = getattr(self.model, order_by)
            query = query.order_by(order_column.desc() if order_desc else order_column)

        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_status(
        self,
        status_value: str,
        tenant_id: Optional[str] = None,
        status_field: str = "status",
    ) -> list[ModelType]:
        """
        ステータスでフィルタして取得

        Args:
            status_value: ステータス値
            tenant_id: テナントID
            status_field: ステータスフィールド名

        Returns:
            エンティティリスト
        """
        status_column = getattr(self.model, status_field)
        conditions = [status_column == status_value]

        if self.tenant_field and tenant_id:
            tenant_column = getattr(self.model, self.tenant_field)
            conditions.append(tenant_column == tenant_id)

        query = select(self.model).where(and_(*conditions))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create(
        self,
        entity: ModelType,
        auto_generate_id: bool = True,
    ) -> ModelType:
        """
        エンティティを作成

        Args:
            entity: 作成するエンティティ
            auto_generate_id: IDを自動生成するか

        Returns:
            作成されたエンティティ
        """
        if auto_generate_id:
            current_id = getattr(entity, self.id_field, None)
            if not current_id:
                setattr(entity, self.id_field, str(uuid4()))

        self.db.add(entity)
        await self.db.flush()
        await self.db.refresh(entity)
        return entity

    async def update(
        self,
        id_value: str,
        update_data: dict[str, Any],
        tenant_id: Optional[str] = None,
    ) -> Optional[ModelType]:
        """
        エンティティを更新

        Args:
            id_value: ID値
            update_data: 更新データ
            tenant_id: テナントID

        Returns:
            更新されたエンティティまたはNone
        """
        id_column = getattr(self.model, self.id_field)
        conditions = [id_column == id_value]

        if self.tenant_field and tenant_id:
            tenant_column = getattr(self.model, self.tenant_field)
            conditions.append(tenant_column == tenant_id)

        await self.db.execute(
            update(self.model)
            .where(and_(*conditions))
            .values(**update_data)
        )
        await self.db.flush()

        return await self.get_by_id(id_value, tenant_id)

    async def soft_delete(
        self,
        id_value: str,
        tenant_id: Optional[str] = None,
        status_field: str = "status",
        deleted_status: str = "deleted",
    ) -> bool:
        """
        論理削除

        Args:
            id_value: ID値
            tenant_id: テナントID
            status_field: ステータスフィールド名
            deleted_status: 削除状態の値

        Returns:
            削除成功フラグ
        """
        result = await self.update(
            id_value,
            {status_field: deleted_status},
            tenant_id,
        )
        return result is not None

    async def exists(
        self,
        id_value: str,
        tenant_id: Optional[str] = None,
    ) -> bool:
        """
        エンティティが存在するか確認

        Args:
            id_value: ID値
            tenant_id: テナントID

        Returns:
            存在フラグ
        """
        entity = await self.get_by_id(id_value, tenant_id)
        return entity is not None

    async def count(
        self,
        tenant_id: Optional[str] = None,
        status_value: Optional[str] = None,
        status_field: str = "status",
    ) -> int:
        """
        エンティティ数をカウント

        Args:
            tenant_id: テナントID
            status_value: フィルタするステータス値
            status_field: ステータスフィールド名

        Returns:
            エンティティ数
        """
        from sqlalchemy import func

        conditions = []

        if self.tenant_field and tenant_id:
            tenant_column = getattr(self.model, self.tenant_field)
            conditions.append(tenant_column == tenant_id)

        if status_value:
            status_column = getattr(self.model, status_field)
            conditions.append(status_column == status_value)

        query = select(func.count()).select_from(self.model)
        if conditions:
            query = query.where(and_(*conditions))

        result = await self.db.execute(query)
        return result.scalar() or 0
