"""
シンプルチャットサービス
SDKを使わない直接Bedrock呼び出しによるチャット管理
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator
from uuid import uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.simple_chat import SimpleChat
from app.models.simple_chat_message import SimpleChatMessage
from app.repositories.simple_chat_repository import (
    SimpleChatMessageRepository,
    SimpleChatRepository,
)
from app.services.aws_config import AWSConfig
from app.services.bedrock_client import BedrockChatClient, SimpleChatTitleGenerator
from app.services.usage_service import UsageService

logger = structlog.get_logger(__name__)


class SimpleChatService:
    """シンプルチャットサービスクラス"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.chat_repo = SimpleChatRepository(db)
        self.message_repo = SimpleChatMessageRepository(db)
        self.aws_config = AWSConfig()
        self.bedrock_client = BedrockChatClient(self.aws_config)
        self.title_generator = SimpleChatTitleGenerator(self.bedrock_client)
        self.usage_service = UsageService(db)

    # ============================================
    # チャット操作
    # ============================================

    async def create_chat(
        self,
        tenant_id: str,
        user_id: str,
        model_id: str,
        application_type: str,
        system_prompt: str,
    ) -> SimpleChat:
        """新規チャットを作成"""
        chat = SimpleChat(
            chat_id=str(uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            model_id=model_id,
            application_type=application_type,
            system_prompt=system_prompt,
            status="active",
        )
        created = await self.chat_repo.create(chat)

        logger.info(
            "シンプルチャット作成",
            chat_id=created.chat_id,
            tenant_id=tenant_id,
            application_type=application_type,
        )
        return created

    async def get_chat_by_id(
        self,
        chat_id: str,
        tenant_id: str,
    ) -> SimpleChat | None:
        """IDでチャットを取得"""
        return await self.chat_repo.get_by_id(chat_id, tenant_id)

    async def get_chats_by_tenant(
        self,
        tenant_id: str,
        user_id: str | None = None,
        application_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SimpleChat], int]:
        """テナントのチャット一覧を取得"""
        return await self.chat_repo.find_by_tenant(
            tenant_id,
            user_id=user_id,
            application_type=application_type,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def update_chat_title(
        self,
        chat_id: str,
        tenant_id: str,
        title: str,
    ) -> SimpleChat | None:
        """チャットのタイトルを更新"""
        chat = await self.chat_repo.get_by_id(chat_id, tenant_id)
        if not chat:
            return None

        chat.title = title
        await self.db.flush()
        await self.db.refresh(chat)
        return chat

    async def delete_chat(
        self,
        chat_id: str,
        tenant_id: str,
    ) -> bool:
        """チャットを削除"""
        return await self.chat_repo.delete(chat_id, tenant_id)

    async def archive_chat(
        self,
        chat_id: str,
        tenant_id: str,
    ) -> SimpleChat | None:
        """チャットをアーカイブ"""
        chat = await self.chat_repo.get_by_id(chat_id, tenant_id)
        if not chat:
            return None

        chat.status = "archived"
        await self.db.flush()
        await self.db.refresh(chat)

        logger.info(
            "シンプルチャットアーカイブ",
            chat_id=chat.chat_id,
            tenant_id=tenant_id,
        )
        return chat

    # ============================================
    # メッセージ操作
    # ============================================

    async def get_messages(self, chat_id: str) -> list[SimpleChatMessage]:
        """チャットのメッセージ一覧を取得"""
        return await self.message_repo.find_by_chat(chat_id)

    async def _save_message(
        self,
        chat_id: str,
        role: str,
        content: str,
    ) -> SimpleChatMessage:
        """メッセージを保存"""
        max_seq = await self.message_repo.get_max_seq(chat_id)

        message = SimpleChatMessage(
            message_id=str(uuid4()),
            chat_id=chat_id,
            message_seq=max_seq + 1,
            role=role,
            content=content,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    # ============================================
    # ストリーミング実行
    # ============================================

    async def stream_message(
        self,
        chat: SimpleChat,
        model: Model,
        user_message: str,
    ) -> AsyncGenerator[dict, None]:
        """メッセージを送信してストリーミング応答を取得"""
        seq = 1

        try:
            # ユーザーメッセージを保存
            await self._save_message(chat.chat_id, "user", user_message)
            await self.db.commit()

            # 過去のメッセージ履歴を取得
            messages = await self.get_messages(chat.chat_id)
            message_history = [
                {"role": msg.role, "content": msg.content} for msg in messages
            ]

            is_first_message = chat.title is None

            # アシスタント応答を収集
            assistant_response = ""
            input_tokens = 0
            output_tokens = 0

            # Bedrock APIでストリーミング
            async for chunk in self.bedrock_client.stream_chat(
                model_id=model.bedrock_model_id,
                system_prompt=chat.system_prompt,
                messages=message_history,
            ):
                if chunk.type == "text_delta" and chunk.content:
                    assistant_response += chunk.content
                    yield {
                        "seq": seq,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event_type": "text_delta",
                        "content": chunk.content,
                    }
                    seq += 1
                elif chunk.type == "metadata":
                    input_tokens = chunk.input_tokens
                    output_tokens = chunk.output_tokens

            # アシスタント応答を保存
            await self._save_message(chat.chat_id, "assistant", assistant_response)

            # タイトル生成（初回のみ）
            title = None
            if is_first_message and assistant_response:
                title = await asyncio.to_thread(
                    self.title_generator.generate, user_message, assistant_response
                )
                await self.update_chat_title(chat.chat_id, chat.tenant_id, title)

            # コスト計算 - Model.calculate_cost を使用
            cost_usd = model.calculate_cost(input_tokens, output_tokens)

            # 使用状況ログを保存
            await self.usage_service.save_usage_log(
                tenant_id=chat.tenant_id,
                user_id=chat.user_id,
                model_id=chat.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                simple_chat_id=chat.chat_id,
            )

            # 完了イベント
            yield {
                "seq": seq,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": "done",
                "title": title,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
                "cost_usd": str(cost_usd),
            }

        except Exception as e:
            logger.error(
                "シンプルチャットストリーミングエラー",
                chat_id=chat.chat_id,
                error=str(e),
            )
            yield {
                "seq": seq,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": "error",
                "message": str(e),
                "error_type": type(e).__name__,
                "recoverable": False,
            }
