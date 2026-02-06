"""
会話・履歴サービス
会話と会話履歴の管理
"""
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message_log import MessageLog
from app.utils.timezone import to_utc


class ConversationService:
    """会話・履歴サービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

    # ============================================
    # 会話操作
    # ============================================

    async def get_conversations_by_tenant(
        self,
        tenant_id: str,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        """
        テナントの会話一覧を取得

        Args:
            tenant_id: テナントID
            user_id: フィルタリング用ユーザーID
            status: フィルタリング用ステータス
            from_date: 開始日時（タイムゾーンなしの場合JSTとして扱う）
            to_date: 終了日時（タイムゾーンなしの場合JSTとして扱う）
            limit: 取得件数
            offset: オフセット

        Returns:
            会話リスト
        """
        query = select(Conversation).where(Conversation.tenant_id == tenant_id)

        # タイムゾーンなしの日時はJSTとして扱い、UTCに変換
        from_date_utc = to_utc(from_date)
        to_date_utc = to_utc(to_date)

        if user_id:
            query = query.where(Conversation.user_id == user_id)
        if status:
            query = query.where(Conversation.status == status)
        if from_date_utc:
            query = query.where(Conversation.created_at >= from_date_utc)
        if to_date_utc:
            query = query.where(Conversation.created_at <= to_date_utc)

        query = query.order_by(Conversation.updated_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_conversation_by_id(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> Optional[Conversation]:
        """
        IDで会話を取得

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID

        Returns:
            会話（存在しない場合はNone）
        """
        query = select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
        user_id: str,
        model_id: str,
        title: Optional[str] = None,
        workspace_enabled: bool = False,
    ) -> Conversation:
        """
        会話を作成

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID
            user_id: ユーザーID
            model_id: モデルID
            title: 会話タイトル
            workspace_enabled: ワークスペース有効フラグ

        Returns:
            作成された会話
        """
        conversation = Conversation(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            user_id=user_id,
            model_id=model_id,
            title=title,
            status="active",
            workspace_enabled=workspace_enabled,
        )
        self.db.add(conversation)
        await self.db.flush()
        await self.db.refresh(conversation)
        return conversation

    async def update_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
        session_id: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[str] = None,
        workspace_enabled: Optional[bool] = None,
    ) -> Optional[Conversation]:
        """
        会話を更新

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID
            session_id: SDKセッションID
            title: タイトル
            status: ステータス
            workspace_enabled: ワークスペース有効フラグ

        Returns:
            更新された会話（存在しない場合はNone）
        """
        conversation = await self.get_conversation_by_id(conversation_id, tenant_id)
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
    ) -> Optional[Conversation]:
        """
        会話のタイトルを更新

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID
            title: 新しいタイトル

        Returns:
            更新された会話（存在しない場合はNone）
        """
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
    ) -> Optional[Conversation]:
        """
        会話のコンテキスト状況を更新

        実行終了時に呼び出され、トークン使用状況と制限到達フラグを更新。

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID
            total_input_tokens: 累積入力トークン数
            total_output_tokens: 累積出力トークン数
            estimated_context_tokens: 推定コンテキストトークン数
            context_limit_reached: コンテキスト制限到達フラグ

        Returns:
            更新された会話（存在しない場合はNone）
        """
        conversation = await self.get_conversation_by_id(conversation_id, tenant_id)
        if not conversation:
            return None

        conversation.total_input_tokens += total_input_tokens
        conversation.total_output_tokens += total_output_tokens
        conversation.estimated_context_tokens = estimated_context_tokens
        conversation.context_limit_reached = context_limit_reached

        self.db.add(conversation)
        await self.db.flush()

        return conversation

    async def archive_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> Optional[Conversation]:
        """
        会話をアーカイブ

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID

        Returns:
            更新された会話（存在しない場合はNone）
        """
        return await self.update_conversation(
            conversation_id, tenant_id, status="archived"
        )

    async def delete_conversation(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> bool:
        """
        会話を削除

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID

        Returns:
            削除成功かどうか
        """
        conversation = await self.get_conversation_by_id(conversation_id, tenant_id)
        if not conversation:
            return False

        # 関連するメッセージログを削除
        await self.db.execute(
            MessageLog.__table__.delete().where(
                MessageLog.conversation_id == conversation_id
            )
        )

        await self.db.delete(conversation)
        return True

    # ============================================
    # メッセージログ操作
    # ============================================

    async def save_message_log(
        self,
        conversation_id: str,
        message_seq: int,
        message_type: str,
        message_subtype: Optional[str],
        content: Optional[dict[str, Any]],
    ) -> MessageLog:
        """
        メッセージログを保存

        Args:
            conversation_id: 会話ID
            message_seq: メッセージ順序
            message_type: メッセージタイプ
            message_subtype: メッセージサブタイプ
            content: メッセージ内容

        Returns:
            保存されたメッセージログ
        """
        log = MessageLog(
            message_id=str(uuid4()),
            conversation_id=conversation_id,
            message_seq=message_seq,
            message_type=message_type,
            message_subtype=message_subtype,
            content=content,
        )
        self.db.add(log)
        await self.db.flush()
        return log

    async def get_message_logs(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> list[MessageLog]:
        """
        会話のメッセージログを取得

        Args:
            conversation_id: 会話ID
            tenant_id: テナントID（権限チェック用）

        Returns:
            メッセージログリスト
        """
        # まず会話の存在確認
        conversation = await self.get_conversation_by_id(conversation_id, tenant_id)
        if not conversation:
            return []

        query = (
            select(MessageLog)
            .where(MessageLog.conversation_id == conversation_id)
            .order_by(MessageLog.message_seq)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_max_message_seq(
        self,
        conversation_id: str,
    ) -> int:
        """
        会話の最大メッセージ順序を取得

        Args:
            conversation_id: 会話ID

        Returns:
            最大メッセージ順序（メッセージがない場合は0）
        """
        query = (
            select(func.max(MessageLog.message_seq))
            .where(MessageLog.conversation_id == conversation_id)
        )
        result = await self.db.execute(query)
        max_seq = result.scalar()
        return max_seq if max_seq is not None else 0

    async def get_latest_turn_number(
        self,
        conversation_id: str,
    ) -> int:
        """
        最新のターン番号を取得
        ターン番号はユーザーメッセージの数として計算

        Args:
            conversation_id: 会話ID

        Returns:
            最新のターン番号（存在しない場合は0）
        """
        query = (
            select(func.count())
            .select_from(MessageLog)
            .where(
                MessageLog.conversation_id == conversation_id,
                MessageLog.message_type == "user",
            )
        )
        result = await self.db.execute(query)
        count = result.scalar()
        return count if count else 0
