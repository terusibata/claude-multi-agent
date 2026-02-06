"""
会話テーブル
ユーザーとエージェント間の会話を管理
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.conversation_file import ConversationFile
    from app.models.model import Model
    from app.models.tenant import Tenant


class Conversation(Base):
    """
    会話テーブル
    アプリケーション層の会話管理とSDKセッションの紐づけ
    会話専用ワークスペース管理を含む
    """
    __tablename__ = "conversations"

    # 会話ID（アプリケーション層の会話ID）
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # SDKセッションID（resume用）
    session_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

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

    # 会話タイトル
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ステータス (active / archived)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )

    # ワークスペース有効フラグ
    workspace_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ワークスペースパス（会話専用ディレクトリ）
    workspace_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ワークスペース作成日時
    workspace_created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 累積入力トークン数
    total_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="累積入力トークン数"
    )

    # 累積出力トークン数
    total_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="累積出力トークン数"
    )

    # 推定コンテキストトークン数（次回リクエスト時のサイズ推定）
    estimated_context_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="推定コンテキストトークン数"
    )

    # コンテキスト制限に到達しているか
    context_limit_reached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="コンテキスト制限到達フラグ"
    )

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # リレーションシップ
    # デフォルトはnoload（必要な箇所でselectinload/joinedloadを明示的に使用）
    tenant: Mapped["Tenant"] = relationship("Tenant", lazy="noload")
    model: Mapped["Model"] = relationship("Model", lazy="noload")
    files: Mapped[list["ConversationFile"]] = relationship(
        "ConversationFile",
        back_populates="conversation",
        lazy="noload",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Conversation(conversation_id={self.conversation_id}, title={self.title})>"
