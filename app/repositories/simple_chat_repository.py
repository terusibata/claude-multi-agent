"""
シンプルチャットリポジトリ
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.simple_chat import SimpleChat
from app.models.simple_chat_message import SimpleChatMessage
from app.repositories.base import BaseRepository


class SimpleChatRepository(BaseRepository[SimpleChat]):
    """シンプルチャットのデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(
            db, SimpleChat, id_field="chat_id", tenant_field="tenant_id"
        )

    async def find_by_tenant(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        application_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SimpleChat], int]:
        """テナントのチャット一覧と総件数を取得"""
        filters = [SimpleChat.tenant_id == tenant_id]
        if user_id:
            filters.append(SimpleChat.user_id == user_id)
        if application_type:
            filters.append(SimpleChat.application_type == application_type)
        if status:
            filters.append(SimpleChat.status == status)

        return await self.find_with_count(
            filters=filters, limit=limit, offset=offset,
        )


class SimpleChatMessageRepository(BaseRepository[SimpleChatMessage]):
    """シンプルチャットメッセージのデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(db, SimpleChatMessage, id_field="message_id")

    async def find_by_chat(self, chat_id: str) -> list[SimpleChatMessage]:
        """チャットのメッセージ一覧を取得（順序付き）"""
        query = (
            select(SimpleChatMessage)
            .where(SimpleChatMessage.chat_id == chat_id)
            .order_by(SimpleChatMessage.message_seq)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_max_seq(self, chat_id: str) -> int:
        """チャットの最大メッセージ順序を取得"""
        query = select(func.max(SimpleChatMessage.message_seq)).where(
            SimpleChatMessage.chat_id == chat_id
        )
        result = await self.db.execute(query)
        max_seq = result.scalar()
        return max_seq if max_seq is not None else 0
