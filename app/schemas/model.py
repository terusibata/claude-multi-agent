"""
モデル定義スキーマ
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ModelBase(BaseModel):
    """モデル定義の共通フィールド"""

    display_name: str = Field(..., description="UIで表示する名称", max_length=200)
    bedrock_model_id: str = Field(
        ...,
        description="AWS BedrockのモデルID（例: us.anthropic.claude-sonnet-4-5-20250929-v1:0）",
        max_length=200,
    )
    model_region: Optional[str] = Field(
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


class ModelCreate(ModelBase):
    """モデル定義作成リクエスト"""

    model_id: str = Field(
        ..., description="内部管理ID（プライマリキー）", max_length=100
    )


class ModelUpdate(BaseModel):
    """モデル定義更新リクエスト"""

    display_name: Optional[str] = Field(None, max_length=200)
    bedrock_model_id: Optional[str] = Field(None, max_length=200)
    model_region: Optional[str] = Field(None, max_length=50)
    input_token_price: Optional[Decimal] = None
    output_token_price: Optional[Decimal] = None
    cache_creation_5m_price: Optional[Decimal] = None
    cache_creation_1h_price: Optional[Decimal] = None
    cache_read_price: Optional[Decimal] = None
    status: Optional[str] = Field(None, pattern="^(active|deprecated)$")


class ModelResponse(ModelBase):
    """モデル定義レスポンス"""

    model_id: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
