"""
表示用キャッシュテーブル
フロントエンドUI表示用の最適化されたデータを保存
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DisplayCache(Base):
    """
    表示用キャッシュテーブル
    UI表示用の最適化されたデータ（ツール詳細は省略）
    高速な表示のため最適化
    """
    __tablename__ = "display_cache"

    # キャッシュID
    cache_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # チャットセッションID
    chat_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("chat_sessions.chat_session_id"),
        nullable=False,
        index=True,
    )

    # ターン番号（1つのセッション内での順序）
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # ユーザー入力テキスト
    user_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # アシスタントの最終応答テキスト
    assistant_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ツール使用サマリー（JSON配列）
    # [{"tool_name": "Read", "status": "completed", "summary": "Read 150 lines"}]
    tools_summary: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # メタデータ（JSON）
    # {"tokens": 1800, "cost_usd": 0.0025, "duration_ms": 5000, "num_turns": 3}
    # 注: 'metadata'はSQLAlchemyの予約語のため、Python属性名は'metadata_'とし、
    # データベースのカラム名は'metadata'として保持
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<DisplayCache(cache_id={self.cache_id}, turn={self.turn_number})>"
