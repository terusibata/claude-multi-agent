"""
セッション・履歴サービス
チャットセッションと会話履歴の管理
"""
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession
from app.models.display_cache import DisplayCache
from app.models.message_log import MessageLog


class SessionService:
    """セッション・履歴サービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

    # ============================================
    # チャットセッション操作
    # ============================================

    async def get_sessions_by_tenant(
        self,
        tenant_id: str,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSession]:
        """
        テナントのセッション一覧を取得

        Args:
            tenant_id: テナントID
            user_id: フィルタリング用ユーザーID
            status: フィルタリング用ステータス
            from_date: 開始日時
            to_date: 終了日時
            limit: 取得件数
            offset: オフセット

        Returns:
            セッションリスト
        """
        query = select(ChatSession).where(ChatSession.tenant_id == tenant_id)

        if user_id:
            query = query.where(ChatSession.user_id == user_id)
        if status:
            query = query.where(ChatSession.status == status)
        if from_date:
            query = query.where(ChatSession.created_at >= from_date)
        if to_date:
            query = query.where(ChatSession.created_at <= to_date)

        query = query.order_by(ChatSession.updated_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_session_by_id(
        self,
        chat_session_id: str,
        tenant_id: str,
    ) -> Optional[ChatSession]:
        """
        IDでセッションを取得

        Args:
            chat_session_id: チャットセッションID
            tenant_id: テナントID

        Returns:
            セッション（存在しない場合はNone）
        """
        query = select(ChatSession).where(
            ChatSession.chat_session_id == chat_session_id,
            ChatSession.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create_session(
        self,
        chat_session_id: str,
        tenant_id: str,
        user_id: str,
        agent_config_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> ChatSession:
        """
        セッションを作成

        Args:
            chat_session_id: チャットセッションID
            tenant_id: テナントID
            user_id: ユーザーID
            agent_config_id: エージェント設定ID
            title: セッションタイトル

        Returns:
            作成されたセッション
        """
        session = ChatSession(
            chat_session_id=chat_session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_config_id=agent_config_id,
            title=title,
            status="active",
        )
        self.db.add(session)
        await self.db.flush()
        await self.db.refresh(session)
        return session

    async def update_session(
        self,
        chat_session_id: str,
        tenant_id: str,
        session_id: Optional[str] = None,
        parent_session_id: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[ChatSession]:
        """
        セッションを更新

        Args:
            chat_session_id: チャットセッションID
            tenant_id: テナントID
            session_id: SDKセッションID
            parent_session_id: 親セッションID
            title: タイトル
            status: ステータス

        Returns:
            更新されたセッション（存在しない場合はNone）
        """
        session = await self.get_session_by_id(chat_session_id, tenant_id)
        if not session:
            return None

        if session_id is not None:
            session.session_id = session_id
        if parent_session_id is not None:
            session.parent_session_id = parent_session_id
        if title is not None:
            session.title = title
        if status is not None:
            session.status = status

        await self.db.flush()
        await self.db.refresh(session)
        return session

    async def archive_session(
        self,
        chat_session_id: str,
        tenant_id: str,
    ) -> Optional[ChatSession]:
        """
        セッションをアーカイブ

        Args:
            chat_session_id: チャットセッションID
            tenant_id: テナントID

        Returns:
            更新されたセッション（存在しない場合はNone）
        """
        return await self.update_session(
            chat_session_id, tenant_id, status="archived"
        )

    async def delete_session(
        self,
        chat_session_id: str,
        tenant_id: str,
    ) -> bool:
        """
        セッションを削除

        Args:
            chat_session_id: チャットセッションID
            tenant_id: テナントID

        Returns:
            削除成功かどうか
        """
        session = await self.get_session_by_id(chat_session_id, tenant_id)
        if not session:
            return False

        # 関連するメッセージログと表示キャッシュも削除
        await self.db.execute(
            MessageLog.__table__.delete().where(
                MessageLog.chat_session_id == chat_session_id
            )
        )
        await self.db.execute(
            DisplayCache.__table__.delete().where(
                DisplayCache.chat_session_id == chat_session_id
            )
        )

        await self.db.delete(session)
        return True

    # ============================================
    # メッセージログ操作
    # ============================================

    async def save_message_log(
        self,
        chat_session_id: str,
        message_seq: int,
        message_type: str,
        message_subtype: Optional[str],
        content: Optional[dict[str, Any]],
    ) -> MessageLog:
        """
        メッセージログを保存

        Args:
            chat_session_id: チャットセッションID
            message_seq: メッセージ順序
            message_type: メッセージタイプ
            message_subtype: メッセージサブタイプ
            content: メッセージ内容

        Returns:
            保存されたメッセージログ
        """
        log = MessageLog(
            message_id=str(uuid4()),
            chat_session_id=chat_session_id,
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
        chat_session_id: str,
        tenant_id: str,
    ) -> list[MessageLog]:
        """
        セッションのメッセージログを取得

        Args:
            chat_session_id: チャットセッションID
            tenant_id: テナントID（権限チェック用）

        Returns:
            メッセージログリスト
        """
        # まずセッションの存在確認
        session = await self.get_session_by_id(chat_session_id, tenant_id)
        if not session:
            return []

        query = (
            select(MessageLog)
            .where(MessageLog.chat_session_id == chat_session_id)
            .order_by(MessageLog.message_seq)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    # ============================================
    # 表示キャッシュ操作
    # ============================================

    async def save_display_cache(
        self,
        chat_session_id: str,
        turn_number: int,
        user_message: Optional[str],
        assistant_message: Optional[str],
        tools_summary: Optional[list[dict]],
        metadata: Optional[dict],
    ) -> DisplayCache:
        """
        表示キャッシュを保存

        Args:
            chat_session_id: チャットセッションID
            turn_number: ターン番号
            user_message: ユーザーメッセージ
            assistant_message: アシスタントメッセージ
            tools_summary: ツールサマリー
            metadata: メタデータ

        Returns:
            保存された表示キャッシュ
        """
        cache = DisplayCache(
            cache_id=str(uuid4()),
            chat_session_id=chat_session_id,
            turn_number=turn_number,
            user_message=user_message,
            assistant_message=assistant_message,
            tools_summary=tools_summary,
            metadata=metadata,
        )
        self.db.add(cache)
        await self.db.flush()
        return cache

    async def get_display_cache(
        self,
        chat_session_id: str,
        tenant_id: str,
    ) -> list[DisplayCache]:
        """
        セッションの表示キャッシュを取得

        Args:
            chat_session_id: チャットセッションID
            tenant_id: テナントID（権限チェック用）

        Returns:
            表示キャッシュリスト
        """
        # まずセッションの存在確認
        session = await self.get_session_by_id(chat_session_id, tenant_id)
        if not session:
            return []

        query = (
            select(DisplayCache)
            .where(DisplayCache.chat_session_id == chat_session_id)
            .order_by(DisplayCache.turn_number)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_latest_turn_number(
        self,
        chat_session_id: str,
    ) -> int:
        """
        最新のターン番号を取得

        Args:
            chat_session_id: チャットセッションID

        Returns:
            最新のターン番号（存在しない場合は0）
        """
        query = (
            select(DisplayCache.turn_number)
            .where(DisplayCache.chat_session_id == chat_session_id)
            .order_by(DisplayCache.turn_number.desc())
            .limit(1)
        )
        result = await self.db.execute(query)
        turn = result.scalar_one_or_none()
        return turn if turn else 0
