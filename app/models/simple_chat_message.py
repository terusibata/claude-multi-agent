"""
シンプルチャットメッセージテーブル
チャット内のメッセージ履歴を管理
"""
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.simple_chat import SimpleChat


class SimpleChatMessage(Base):
    """
    シンプルチャットメッセージテーブル
    チャット内の全メッセージを順序付きで保存
    """
    __tablename__ = "simple_chat_messages"

    # メッセージID
    message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # チャットID
    chat_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("simple_chats.chat_id"),
        nullable=False,
        index=True,
    )

    # メッセージ順序
    message_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # ロール (user / assistant)
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    # メッセージ内容（テキスト）
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # リレーションシップ
    chat: Mapped["SimpleChat"] = relationship(
        "SimpleChat", back_populates="messages"
    )

    def __repr__(self) -> str:
        return f"<SimpleChatMessage(message_id={self.message_id}, role={self.role})>"
