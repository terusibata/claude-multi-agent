"""
メッセージログリポジトリ
"""
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_log import MessageLog
from app.repositories.base import BaseRepository


class MessageLogRepository(BaseRepository[MessageLog]):
    """メッセージログのデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(db, MessageLog, id_field="message_id")

    async def find_by_conversation(
        self, conversation_id: str
    ) -> list[MessageLog]:
        """会話のメッセージログを取得（順序付き）"""
        query = (
            select(MessageLog)
            .where(MessageLog.conversation_id == conversation_id)
            .order_by(MessageLog.message_seq)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_max_seq(self, conversation_id: str) -> int:
        """会話の最大メッセージ順序を取得"""
        query = select(func.max(MessageLog.message_seq)).where(
            MessageLog.conversation_id == conversation_id
        )
        result = await self.db.execute(query)
        max_seq = result.scalar()
        return max_seq if max_seq is not None else 0

    async def count_by_type(
        self, conversation_id: str, message_type: str
    ) -> int:
        """指定タイプのメッセージ数をカウント"""
        query = (
            select(func.count())
            .select_from(MessageLog)
            .where(
                MessageLog.conversation_id == conversation_id,
                MessageLog.message_type == message_type,
            )
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def save(
        self,
        message_id: str,
        conversation_id: str,
        message_seq: int,
        message_type: str,
        message_subtype: str | None,
        content: dict[str, Any] | None,
    ) -> MessageLog:
        """メッセージログを保存"""
        log = MessageLog(
            message_id=message_id,
            conversation_id=conversation_id,
            message_seq=message_seq,
            message_type=message_type,
            message_subtype=message_subtype,
            content=content,
        )
        self.db.add(log)
        await self.db.flush()
        return log
