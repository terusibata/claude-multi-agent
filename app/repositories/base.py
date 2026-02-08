"""
ベースリポジトリ
共通のデータベース操作を提供
"""
from typing import Any, Generic, TypeVar
from uuid import uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):
    """
    ベースリポジトリクラス

    各エンティティ固有のリポジトリはこのクラスを継承し、
    id_field と tenant_field を設定する。

    使用例:
        class TenantRepository(BaseRepository[Tenant]):
            def __init__(self, db: AsyncSession):
                super().__init__(db, Tenant, "tenant_id")
    """

    def __init__(
        self,
        db: AsyncSession,
        model: type[ModelType],
        id_field: str = "id",
        tenant_field: str | None = None,
    ):
        self.db = db
        self.model = model
        self.id_field = id_field
        self.tenant_field = tenant_field

    def _build_conditions(
        self,
        id_value: str | None = None,
        tenant_id: str | None = None,
        **extra_filters: Any,
    ) -> list:
        """WHERE条件リストを構築"""
        conditions = []

        if id_value is not None:
            id_column = getattr(self.model, self.id_field)
            conditions.append(id_column == id_value)

        if self.tenant_field and tenant_id is not None:
            tenant_column = getattr(self.model, self.tenant_field)
            conditions.append(tenant_column == tenant_id)

        for field_name, value in extra_filters.items():
            if value is not None:
                column = getattr(self.model, field_name, None)
                if column is not None:
                    conditions.append(column == value)

        return conditions

    async def get_by_id(
        self,
        id_value: str,
        tenant_id: str | None = None,
    ) -> ModelType | None:
        """IDでエンティティを取得"""
        conditions = self._build_conditions(id_value=id_value, tenant_id=tenant_id)
        query = select(self.model).where(and_(*conditions))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_all(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
        order_desc: bool = False,
        **filters: Any,
    ) -> list[ModelType]:
        """エンティティ一覧を取得"""
        conditions = self._build_conditions(tenant_id=tenant_id, **filters)
        query = select(self.model)

        if conditions:
            query = query.where(and_(*conditions))

        if order_by:
            order_column = getattr(self.model, order_by)
            query = query.order_by(
                order_column.desc() if order_desc else order_column
            )

        query = query.limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create(self, entity: ModelType) -> ModelType:
        """エンティティを作成"""
        current_id = getattr(entity, self.id_field, None)
        if not current_id:
            setattr(entity, self.id_field, str(uuid4()))

        self.db.add(entity)
        await self.db.flush()
        await self.db.refresh(entity)
        return entity

    async def update_fields(
        self,
        id_value: str,
        tenant_id: str | None = None,
        **fields: Any,
    ) -> ModelType | None:
        """エンティティのフィールドを更新"""
        # None以外のフィールドのみ更新
        update_data = {k: v for k, v in fields.items() if v is not None}
        if not update_data:
            return await self.get_by_id(id_value, tenant_id)

        conditions = self._build_conditions(id_value=id_value, tenant_id=tenant_id)
        await self.db.execute(
            update(self.model).where(and_(*conditions)).values(**update_data)
        )
        await self.db.flush()
        return await self.get_by_id(id_value, tenant_id)

    async def delete(
        self,
        id_value: str,
        tenant_id: str | None = None,
    ) -> bool:
        """エンティティを削除"""
        entity = await self.get_by_id(id_value, tenant_id)
        if not entity:
            return False
        await self.db.delete(entity)
        return True

    async def exists(
        self,
        id_value: str,
        tenant_id: str | None = None,
    ) -> bool:
        """エンティティが存在するか確認"""
        entity = await self.get_by_id(id_value, tenant_id)
        return entity is not None

    async def count(
        self,
        tenant_id: str | None = None,
        **filters: Any,
    ) -> int:
        """エンティティ数をカウント"""
        conditions = self._build_conditions(tenant_id=tenant_id, **filters)
        query = select(func.count()).select_from(self.model)
        if conditions:
            query = query.where(and_(*conditions))
        result = await self.db.execute(query)
        return result.scalar() or 0
