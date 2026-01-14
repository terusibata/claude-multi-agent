"""
メッセージログテーブル
ストリーミング中のメッセージを完全に記録（バックエンドDB保存用）
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessageLog(Base):
    """
    メッセージログテーブル（完全ログ）
    ストリーミング中の全メッセージを保存
    デバッグ・トラブルシューティング・監査用
    """
    __tablename__ = "messages_log"

    # メッセージID
    message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # 会話ID
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("conversations.conversation_id"),
        nullable=False,
        index=True,
    )

    # メッセージ順序
    message_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # メッセージタイプ
    # system / assistant / user_result / result / user
    message_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # メッセージサブタイプ
    # init / finish / text / tool_use / text_delta など
    message_subtype: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # メッセージ内容（JSON）
    content: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # タイムスタンプ
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<MessageLog(message_id={self.message_id}, type={self.message_type})>"
