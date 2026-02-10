"""
モデル定義スキーマ
"""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ModelBase(BaseModel):
    """モデル定義の共通フィールド"""

    display_name: str = Field(..., description="UIで表示する名称", max_length=200)
    bedrock_model_id: str = Field(
        ...,
        description="AWS BedrockのモデルID（例: us.anthropic.claude-sonnet-4-5-20250929-v1:0）",
        max_length=200,
    )
    model_region: str | None = Field(
        None, description="モデルのデプロイリージョン", max_length=50
    )
    input_token_price: Decimal = Field(
        default=Decimal("0"), description="入力トークン単価 (USD/1Kトークン) - AWS Bedrock公式価格形式"
    )
    output_token_price: Decimal = Field(
        default=Decimal("0"), description="出力トークン単価 (USD/1Kトークン) - AWS Bedrock公式価格形式"
    )
    cache_creation_5m_price: Decimal = Field(
        default=Decimal("0"), description="5分キャッシュ作成単価 (USD/1Kトークン、通常は入力価格×1.25)"
    )
    cache_creation_1h_price: Decimal = Field(
        default=Decimal("0"), description="1時間キャッシュ作成単価 (USD/1Kトークン、通常は入力価格×2.0)"
    )
    cache_read_price: Decimal = Field(
        default=Decimal("0"), description="キャッシュ読込単価 (USD/1Kトークン、通常は入力価格×0.1)"
    )
    context_window: int = Field(
        default=200000, description="Context Window上限（トークン）"
    )
    max_output_tokens: int = Field(
        default=64000, description="最大出力トークン数"
    )
    supports_extended_context: bool = Field(
        default=False, description="拡張Context Window（1M等）対応可否"
    )
    extended_context_window: int | None = Field(
        default=None, description="拡張Context Window上限（トークン）"
    )


class ModelCreate(ModelBase):
    """モデル定義作成リクエスト"""

    model_id: str = Field(
        ..., description="内部管理ID（プライマリキー）", max_length=100
    )


class ModelUpdate(BaseModel):
    """モデル定義更新リクエスト"""

    display_name: str | None = Field(None, max_length=200)
    bedrock_model_id: str | None = Field(None, max_length=200)
    model_region: str | None = Field(None, max_length=50)
    input_token_price: Decimal | None = None
    output_token_price: Decimal | None = None
    cache_creation_5m_price: Decimal | None = None
    cache_creation_1h_price: Decimal | None = None
    cache_read_price: Decimal | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_extended_context: bool | None = None
    extended_context_window: int | None = None
    status: str | None = Field(None, pattern="^(active|deprecated)$")


class ModelResponse(ModelBase):
    """モデル定義レスポンス"""

    model_id: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
