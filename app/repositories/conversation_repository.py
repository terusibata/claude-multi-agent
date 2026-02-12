"""
会話リポジトリ
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_file import ConversationFile
from app.models.message_log import MessageLog
from app.repositories.base import BaseRepository
from app.utils.timezone import to_utc


class ConversationRepository(BaseRepository[Conversation]):
    """会話のデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(
            db, Conversation, id_field="conversation_id", tenant_field="tenant_id"
        )

    async def find_by_tenant(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        status: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Conversation], int]:
        """テナントの会話一覧と総件数を取得"""
        from_date_utc = to_utc(from_date)
        to_date_utc = to_utc(to_date)

        filters = [Conversation.tenant_id == tenant_id]
        if user_id:
            filters.append(Conversation.user_id == user_id)
        if status:
            filters.append(Conversation.status == status)
        if from_date_utc:
            filters.append(Conversation.created_at >= from_date_utc)
        if to_date_utc:
            filters.append(Conversation.created_at <= to_date_utc)

        return await self.find_with_count(
            filters=filters, limit=limit, offset=offset,
        )

    async def update_context_status(
        self,
        conversation_id: str,
        tenant_id: str,
        total_input_tokens: int,
        total_output_tokens: int,
        estimated_context_tokens: int,
        context_limit_reached: bool,
    ) -> Conversation | None:
        """コンテキスト状況を更新"""
        conversation = await self.get_by_id(conversation_id, tenant_id)
        if not conversation:
            return None

        conversation.total_input_tokens = (
            conversation.total_input_tokens or 0
        ) + total_input_tokens
        conversation.total_output_tokens = (
            conversation.total_output_tokens or 0
        ) + total_output_tokens
        conversation.estimated_context_tokens = (
            conversation.estimated_context_tokens or 0
        ) + estimated_context_tokens
        conversation.context_limit_reached = context_limit_reached

        self.db.add(conversation)
        await self.db.flush()
        return conversation

    async def delete_with_related(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> bool:
        """会話と関連データを削除"""
        conversation = await self.get_by_id(conversation_id, tenant_id)
        if not conversation:
            return False

        # 関連するConversationFileレコードを削除
        await self.db.execute(
            ConversationFile.__table__.delete().where(
                ConversationFile.conversation_id == conversation_id
            )
        )

        # 関連するメッセージログを削除
        await self.db.execute(
            MessageLog.__table__.delete().where(
                MessageLog.conversation_id == conversation_id
            )
        )

        await self.db.delete(conversation)
        return True
