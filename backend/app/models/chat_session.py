"""
チャットセッションテーブル
ユーザーとエージェント間の会話セッションを管理
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ChatSession(Base):
    """
    チャットセッションテーブル
    アプリケーション層のセッション管理とSDKセッションの紐づけ
    """
    __tablename__ = "chat_sessions"

    # チャットセッションID（アプリケーション層のセッションID）
    chat_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # SDKセッションID（resume用）
    session_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # 親セッションID（fork時）
    parent_session_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # テナントID
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # ユーザーID
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # 使用したエージェント設定ID
    agent_config_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("agent_configs.agent_config_id"),
        nullable=True,
    )

    # セッションタイトル
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ステータス (active / archived)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # リレーションシップ
    agent_config = relationship("AgentConfig", lazy="selectin")

    def __repr__(self) -> str:
        return f"<ChatSession(chat_session_id={self.chat_session_id}, title={self.title})>"
