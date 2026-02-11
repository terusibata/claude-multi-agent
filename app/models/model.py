"""
モデル定義テーブル
利用可能なAIモデルとその料金情報を管理
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, DateTime, Enum, Integer, String, func
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
    model_region: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Context Window上限（トークン）
    # Claude 4.5系は200,000トークンがデフォルト
    context_window: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200000,
        comment="Context Window上限（トークン）"
    )

    # 最大出力トークン数
    # Claude 4.5系は64,000トークンがデフォルト
    max_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=64000,
        comment="最大出力トークン数"
    )

    # 拡張Context Window対応可否（1M beta等）
    supports_extended_context: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="拡張Context Window（1M等）対応可否"
    )

    # 拡張Context Window時の上限（対応している場合のみ）
    extended_context_window: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="拡張Context Window上限（トークン）"
    )

    # 入力トークン単価 (USD/1Kトークン) - AWS Bedrock公式価格形式
    input_token_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # 出力トークン単価 (USD/1Kトークン) - AWS Bedrock公式価格形式
    output_token_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # 5分キャッシュ作成単価 (USD/1Kトークン) - AWS Bedrock公式価格形式
    # 通常は入力トークン価格の1.25倍
    cache_creation_5m_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # 1時間キャッシュ作成単価 (USD/1Kトークン) - AWS Bedrock公式価格形式
    # 通常は入力トークン価格の2.0倍
    cache_creation_1h_price: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 6), nullable=False, default=Decimal("0")
    )

    # キャッシュ読込単価 (USD/1Kトークン) - AWS Bedrock公式価格形式
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
        # AWS Bedrockは USD/1Kトークン で価格設定されている
        thousand = Decimal("1000")

        # 1Kトークンあたりの単価から計算
        input_cost = (Decimal(input_tokens) / thousand) * self.input_token_price
        output_cost = (Decimal(output_tokens) / thousand) * self.output_token_price
        cache_5m_cost = (Decimal(cache_creation_5m_tokens) / thousand) * self.cache_creation_5m_price
        cache_1h_cost = (Decimal(cache_creation_1h_tokens) / thousand) * self.cache_creation_1h_price
        cache_read_cost = (Decimal(cache_read_tokens) / thousand) * self.cache_read_price

        return input_cost + output_cost + cache_5m_cost + cache_1h_cost + cache_read_cost

    def __repr__(self) -> str:
        return f"<Model(model_id={self.model_id}, display_name={self.display_name})>"
