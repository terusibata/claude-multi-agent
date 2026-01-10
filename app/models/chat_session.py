"""
チャットセッションテーブル
ユーザーとエージェント間の会話セッションを管理
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.session_file import SessionFile


class ChatSession(Base):
    """
    チャットセッションテーブル
    アプリケーション層のセッション管理とSDKセッションの紐づけ
    セッション専用ワークスペース管理を含む
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

    # ワークスペース有効フラグ
    workspace_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ワークスペースパス（セッション専用ディレクトリ）
    workspace_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ワークスペース作成日時
    workspace_created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    files: Mapped[list["SessionFile"]] = relationship(
        "SessionFile",
        back_populates="chat_session",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ChatSession(chat_session_id={self.chat_session_id}, title={self.title})>"
