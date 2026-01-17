"""
モデル定義テーブル
利用可能なAIモデルとその料金情報を管理
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ModelStatus(str, Enum):
    """モデルステータス"""
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class Model(Base):
    """
    モデル定義テーブル
    AWS Bedrockで利用可能なモデルの定義と料金情報を管理
    """
    __tablename__ = "models"

    # 内部管理ID（プライマリキー）
    model_id: Mapped[str] = mapped_column(String(100), primary_key=True)

    # 表示名（UIで表示する名称）
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # AWS BedrockのモデルID
    # 例: us.anthropic.claude-sonnet-4-5-20250929-v1:0
    # 例: global.anthropic.claude-sonnet-4-5-20250929-v1:0
    bedrock_model_id: Mapped[str] = mapped_column(String(200), nullable=False)

    # モデルのデプロイリージョン（オプション）
    model_region: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 入力トークン単価 (USD/1Mトークン)
    input_token_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # 出力トークン単価 (USD/1Mトークン)
    output_token_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # 5分キャッシュ作成単価 (USD/1Mトークン)
    # 通常は入力トークン価格の1.25倍
    cache_creation_5m_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # 1時間キャッシュ作成単価 (USD/1Mトークン)
    # 通常は入力トークン価格の2.0倍
    cache_creation_1h_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # キャッシュ読込単価 (USD/1Mトークン)
    # 通常は入力トークン価格の0.1倍（5分/1時間共通）
    cache_read_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # ステータス (active / deprecated)
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

    def calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_creation_5m_tokens: int = 0,
        cache_creation_1h_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> Decimal:
        """
        トークン数からコストを計算

        Args:
            input_tokens: 入力トークン数
            output_tokens: 出力トークン数
            cache_creation_5m_tokens: 5分キャッシュ作成トークン数
            cache_creation_1h_tokens: 1時間キャッシュ作成トークン数
            cache_read_tokens: キャッシュ読み込みトークン数

        Returns:
            コスト（USD）
        """
        million = Decimal("1000000")

        # 1Mトークンあたりの単価から計算
        input_cost = (Decimal(input_tokens) / million) * self.input_token_price
        output_cost = (Decimal(output_tokens) / million) * self.output_token_price
        cache_5m_cost = (Decimal(cache_creation_5m_tokens) / million) * self.cache_creation_5m_price
        cache_1h_cost = (Decimal(cache_creation_1h_tokens) / million) * self.cache_creation_1h_price
        cache_read_cost = (Decimal(cache_read_tokens) / million) * self.cache_read_price

        return input_cost + output_cost + cache_5m_cost + cache_1h_cost + cache_read_cost

    def __repr__(self) -> str:
        return f"<Model(model_id={self.model_id}, display_name={self.display_name})>"
