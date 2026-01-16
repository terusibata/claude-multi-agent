"""
テナントテーブル
テナントごとの設定を管理
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Tenant(Base):
    """
    テナントテーブル
    テナントごとの基本設定（システムプロンプト、デフォルトモデル）を管理
    """
    __tablename__ = "tenants"

    # テナントID（外部システムで管理されるID）
    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True)

    # システムプロンプト
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # デフォルトモデルID
    model_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        ForeignKey("models.model_id"),
        nullable=True,
    )

    # ステータス (active / inactive)
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
    model = relationship("Model", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Tenant(tenant_id={self.tenant_id})>"
