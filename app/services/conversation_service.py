"""
会話サービス
会話の管理（メッセージログはMessageLogServiceに分離済み）
"""
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.repositories.conversation_repository import ConversationRepository

logger = structlog.get_logger(__name__)


class ConversationService:
    """会話サービスクラス"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ConversationRepository(db)

    async def get_conversations_by_tenant(
        self,
        tenant_id: str,
        user_id: str | None = None,
        status: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Conversation], int]:
        """テナントの会話一覧と総件数を取得"""
        return await self.repo.find_by_tenant(
            tenant_id,
            user_id=user_id,
            status=status,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )

    async def get_conversation_by_id(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> Conversation | None:
        """IDで会話を取得"""
        return await self.repo.get_by_id(conversation_id, tenant_id)

    async def create_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
        user_id: str,
        model_id: str,
        title: str | None = None,
        workspace_enabled: bool = True,
    ) -> Conversation:
        """会話を作成"""
        conversation = Conversation(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            user_id=user_id,
            model_id=model_id,
            title=title,
            status="active",
            workspace_enabled=workspace_enabled,
        )
        return await self.repo.create(conversation)

    async def update_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
        session_id: str | None = None,
        title: str | None = None,
        status: str | None = None,
        workspace_enabled: bool | None = None,
    ) -> Conversation | None:
        """会話を更新"""
        conversation = await self.repo.get_by_id(conversation_id, tenant_id)
        if not conversation:
            return None

        if session_id is not None:
            conversation.session_id = session_id
        if title is not None:
            conversation.title = title
        if status is not None:
            conversation.status = status
        if workspace_enabled is not None:
            conversation.workspace_enabled = workspace_enabled

        await self.db.flush()
        await self.db.refresh(conversation)
        return conversation

    async def update_conversation_title(
        self,
        conversation_id: str,
        tenant_id: str,
        title: str,
    ) -> Conversation | None:
        """会話のタイトルを更新"""
        return await self.update_conversation(
            conversation_id, tenant_id, title=title
        )

    async def update_conversation_context_status(
        self,
        conversation_id: str,
        tenant_id: str,
        total_input_tokens: int,
        total_output_tokens: int,
        estimated_context_tokens: int,
        context_limit_reached: bool,
    ) -> Conversation | None:
        """会話のコンテキスト状況を更新"""
        return await self.repo.update_context_status(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            estimated_context_tokens=estimated_context_tokens,
            context_limit_reached=context_limit_reached,
        )

    async def archive_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> Conversation | None:
        """会話をアーカイブ"""
        return await self.update_conversation(
            conversation_id, tenant_id, status="archived"
        )

    async def delete_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> bool:
        """会話を削除（S3ファイル + DB関連レコード含む）"""
        from app.config import get_settings

        _settings = get_settings()
        if _settings.s3_bucket_name:
            try:
                from app.services.workspace.s3_storage import S3StorageBackend

                s3 = S3StorageBackend()
                await s3.delete_prefix(tenant_id, conversation_id)
            except Exception as e:
                logger.warning(
                    "S3ワークスペースファイル削除エラー（続行）",
                    conversation_id=conversation_id,
                    error=str(e),
                )

        return await self.repo.delete_with_related(conversation_id, tenant_id)

