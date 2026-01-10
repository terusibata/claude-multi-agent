"""
アーティファクトテーブル
エージェントが生成したファイルやコンテンツを管理
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Artifact(Base):
    """
    アーティファクトテーブル
    エージェントがWrite/NotebookEditなどのツールで作成したファイルを管理
    """
    __tablename__ = "artifacts"

    # アーティファクトID
    artifact_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # チャットセッションID
    chat_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("chat_sessions.chat_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ターン番号（セッション内での順序）
    turn_number: Mapped[int] = mapped_column(nullable=False)

    # ツール実行ログID（参照用、オプショナル）
    tool_execution_log_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tool_execution_logs.tool_execution_log_id", ondelete="SET NULL"),
        nullable=True,
    )

    # アーティファクトタイプ (file / notebook / image / code など)
    artifact_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="file"
    )

    # ファイル名
    filename: Mapped[str] = mapped_column(String(500), nullable=False)

    # ファイルパス（ローカルストレージ用）
    file_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    # S3キー（S3ストレージ用）
    s3_key: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    # ファイル内容（小さいファイルの場合はDB保存も可）
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # MIMEタイプ
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ファイルサイズ（バイト）
    file_size: Mapped[Optional[int]] = mapped_column(nullable=True)

    # ツール名（Write, NotebookEdit など）
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)

    # タイトル・説明（フロントエンド表示用）
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # リレーションシップ
    chat_session = relationship("ChatSession", lazy="selectin")
    tool_execution_log = relationship("ToolExecutionLog", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Artifact(artifact_id={self.artifact_id}, filename={self.filename}, type={self.artifact_type})>"
