"""
シンプルチャットサービス
SDKを使わない直接Bedrock呼び出しによるチャット管理
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator, Optional
from uuid import uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.simple_chat import SimpleChat
from app.models.simple_chat_message import SimpleChatMessage
from app.services.bedrock_client import BedrockChatClient, SimpleChatTitleGenerator
from app.services.execute.aws_config import AWSConfig
from app.services.usage_service import UsageService

logger = structlog.get_logger(__name__)


class SimpleChatService:
    """シンプルチャットサービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db
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
        """
        新規チャットを作成

        Args:
            tenant_id: テナントID
            user_id: ユーザーID
            model_id: モデルID
            application_type: アプリケーションタイプ
            system_prompt: システムプロンプト

        Returns:
            作成されたチャット
        """
        chat = SimpleChat(
            chat_id=str(uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            model_id=model_id,
            application_type=application_type,
            system_prompt=system_prompt,
            status="active",
        )
        self.db.add(chat)
        await self.db.flush()
        await self.db.refresh(chat)

        logger.info(
            "シンプルチャット作成",
            chat_id=chat.chat_id,
            tenant_id=tenant_id,
            application_type=application_type,
        )

        return chat

    async def get_chat_by_id(
        self,
        chat_id: str,
        tenant_id: str,
    ) -> Optional[SimpleChat]:
        """
        IDでチャットを取得

        Args:
            chat_id: チャットID
            tenant_id: テナントID

        Returns:
            チャット（存在しない場合はNone）
        """
        query = select(SimpleChat).where(
            SimpleChat.chat_id == chat_id,
            SimpleChat.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_chats_by_tenant(
        self,
        tenant_id: str,
        user_id: Optional[str] = None,
        application_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SimpleChat], int]:
        """
        テナントのチャット一覧を取得

        Args:
            tenant_id: テナントID
            user_id: フィルタリング用ユーザーID
            application_type: フィルタリング用アプリケーションタイプ
            status: フィルタリング用ステータス
            limit: 取得件数
            offset: オフセット

        Returns:
            (チャットリスト, 総件数)
        """
        # ベースクエリ
        base_query = select(SimpleChat).where(SimpleChat.tenant_id == tenant_id)

        if user_id:
            base_query = base_query.where(SimpleChat.user_id == user_id)
        if application_type:
            base_query = base_query.where(SimpleChat.application_type == application_type)
        if status:
            base_query = base_query.where(SimpleChat.status == status)

        # 総件数取得
        count_query = select(func.count()).select_from(base_query.subquery())
        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        # データ取得
        query = base_query.order_by(SimpleChat.updated_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        chats = list(result.scalars().all())

        return chats, total

    async def update_chat_title(
        self,
        chat_id: str,
        tenant_id: str,
        title: str,
    ) -> Optional[SimpleChat]:
        """
        チャットのタイトルを更新

        Args:
            chat_id: チャットID
            tenant_id: テナントID
            title: 新しいタイトル

        Returns:
            更新されたチャット
        """
        chat = await self.get_chat_by_id(chat_id, tenant_id)
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
        """
        チャットを削除

        Args:
            chat_id: チャットID
            tenant_id: テナントID

        Returns:
            削除成功かどうか
        """
        chat = await self.get_chat_by_id(chat_id, tenant_id)
        if not chat:
            return False

        await self.db.delete(chat)
        return True

    async def archive_chat(
        self,
        chat_id: str,
        tenant_id: str,
    ) -> Optional[SimpleChat]:
        """
        チャットをアーカイブ

        Args:
            chat_id: チャットID
            tenant_id: テナントID

        Returns:
            更新されたチャット（存在しない場合はNone）
        """
        chat = await self.get_chat_by_id(chat_id, tenant_id)
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

    async def get_messages(
        self,
        chat_id: str,
    ) -> list[SimpleChatMessage]:
        """
        チャットのメッセージ一覧を取得

        Args:
            chat_id: チャットID

        Returns:
            メッセージリスト
        """
        query = (
            select(SimpleChatMessage)
            .where(SimpleChatMessage.chat_id == chat_id)
            .order_by(SimpleChatMessage.message_seq)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _save_message(
        self,
        chat_id: str,
        role: str,
        content: str,
    ) -> SimpleChatMessage:
        """
        メッセージを保存

        Args:
            chat_id: チャットID
            role: ロール (user / assistant)
            content: メッセージ内容

        Returns:
            保存されたメッセージ
        """
        # 最大シーケンス番号を取得
        max_seq_query = (
            select(func.max(SimpleChatMessage.message_seq))
            .where(SimpleChatMessage.chat_id == chat_id)
        )
        result = await self.db.execute(max_seq_query)
        max_seq = result.scalar() or 0

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
        """
        メッセージを送信してストリーミング応答を取得

        Args:
            chat: チャット
            model: モデル
            user_message: ユーザーメッセージ

        Yields:
            ストリーミングイベント
        """
        seq = 1

        try:
            # ユーザーメッセージを保存
            await self._save_message(chat.chat_id, "user", user_message)

            # 過去のメッセージ履歴を取得
            messages = await self.get_messages(chat.chat_id)
            message_history = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]

            # タイトルが未設定かチェック（初回判定）
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
                title = self.title_generator.generate(user_message, assistant_response)
                await self.update_chat_title(chat.chat_id, chat.tenant_id, title)

            # コスト計算
            cost_usd = self._calculate_cost(model, input_tokens, output_tokens)

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

    def _calculate_cost(
        self,
        model: Model,
        input_tokens: int,
        output_tokens: int,
    ) -> Decimal:
        """
        コストを計算

        Args:
            model: モデル
            input_tokens: 入力トークン数
            output_tokens: 出力トークン数

        Returns:
            コスト（USD）
        """
        # 料金は1Kトークンあたりの価格
        input_cost = (Decimal(input_tokens) / 1000) * model.input_token_price
        output_cost = (Decimal(output_tokens) / 1000) * model.output_token_price
        return input_cost + output_cost
