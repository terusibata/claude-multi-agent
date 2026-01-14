"""
使用状況ログテーブル
テナント・ユーザーごとのトークン使用量とコストを記録
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy import DECIMAL, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UsageLog(Base):
    """
    使用状況ログテーブル
    トークン使用量とコストの記録・分析用
    """
    __tablename__ = "usage_logs"

    # 使用状況ログID
    usage_log_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

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

    # 使用したモデルID
    model_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("models.model_id"),
        nullable=False,
    )

    # SDKセッションID
    session_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # 会話ID
    conversation_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("conversations.conversation_id"),
        nullable=True,
    )

    # 入力トークン数
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 出力トークン数
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # キャッシュ作成トークン数
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # キャッシュ読み込みトークン数
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 合計トークン数
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # コスト（USD）
    cost_usd: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # 実行日時
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<UsageLog(usage_log_id={self.usage_log_id}, cost_usd={self.cost_usd})>"
