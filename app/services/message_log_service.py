"""
メッセージログサービス
メッセージログの管理を専門に担当
"""
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_log import MessageLog
from app.repositories.message_log_repository import MessageLogRepository


class MessageLogService:
    """メッセージログサービスクラス"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = MessageLogRepository(db)

    async def save_message_log(
        self,
        conversation_id: str,
        message_seq: int,
        message_type: str,
        message_subtype: str | None,
        content: dict[str, Any] | None,
    ) -> MessageLog:
        """メッセージログを保存"""
        return await self.repo.save(
            message_id=str(uuid4()),
            conversation_id=conversation_id,
            message_seq=message_seq,
            message_type=message_type,
            message_subtype=message_subtype,
            content=content,
        )

    async def get_message_logs(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> list[MessageLog]:
        """
        会話のメッセージログを取得

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID（権限チェック用、将来の拡張向け）
        """
        return await self.repo.find_by_conversation(conversation_id)

    async def get_max_message_seq(self, conversation_id: str) -> int:
        """会話の最大メッセージ順序を取得"""
        return await self.repo.get_max_seq(conversation_id)

    async def get_latest_turn_number(self, conversation_id: str) -> int:
        """最新のターン番号を取得（ユーザーメッセージ数）"""
        return await self.repo.count_by_type(conversation_id, "user")
