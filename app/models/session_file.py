"""
セッションファイルテーブル
セッション内のファイル（ユーザーアップロード、AI作成）を管理
バージョン管理対応
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, BigInteger, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SessionFile(Base):
    """
    セッションファイルテーブル
    セッション専用ワークスペース内のファイル管理
    """
    __tablename__ = "session_files"

    # ファイルID
    file_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # チャットセッションID
    chat_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("chat_sessions.chat_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ワークスペース内の相対パス（例: "uploads/data.csv" or "outputs/result.txt"）
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)

    # 元のファイル名
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)

    # ファイルサイズ（バイト）
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # MIMEタイプ
    mime_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # バージョン番号（同一パスの場合インクリメント）
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ファイルソース: "user_upload" | "ai_created" | "ai_modified"
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="user_upload")

    # Presented fileフラグ（AIがユーザーに提示したいファイル）
    is_presented: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ファイルチェックサム（SHA256）
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ファイル説明（AI作成時のコンテキスト）
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ステータス: "active" | "deleted"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # リレーションシップ
    chat_session = relationship("ChatSession", back_populates="files", lazy="selectin")

    def __repr__(self) -> str:
        return f"<SessionFile(file_id={self.file_id}, path={self.file_path}, v{self.version})>"
