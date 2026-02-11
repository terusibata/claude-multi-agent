"""
シンプルチャットテーブル
SDKを使わない直接Bedrock呼び出しによるチャットセッション管理
"""
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.model import Model
    from app.models.simple_chat_message import SimpleChatMessage
    from app.models.tenant import Tenant


class SimpleChat(Base):
    """
    シンプルチャットテーブル
    ワークスペースやツールを使わない、テキストのみのチャット管理
    """
    __tablename__ = "simple_chats"

    # チャットID
    chat_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # テナントID
    tenant_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("tenants.tenant_id"),
        nullable=False,
        index=True,
    )

    # ユーザーID
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # 使用するモデルID
    model_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("models.model_id"),
        nullable=False,
    )

    # アプリケーションタイプ（用途識別子）
    # 例: translationApp, summarizer, chatbot など
    application_type: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )

    # システムプロンプト
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)

    # チャットタイトル（自動生成）
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ステータス (active / archived)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # リレーションシップ
    tenant: Mapped["Tenant"] = relationship("Tenant", lazy="selectin")
    model: Mapped["Model"] = relationship("Model", lazy="selectin")
    messages: Mapped[list["SimpleChatMessage"]] = relationship(
        "SimpleChatMessage",
        back_populates="chat",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="SimpleChatMessage.message_seq",
    )

    def __repr__(self) -> str:
        return f"<SimpleChat(chat_id={self.chat_id}, application_type={self.application_type})>"
