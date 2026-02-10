"""
テナントスキーマ
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TenantCreateRequest(BaseModel):
    """テナント作成リクエスト"""

    tenant_id: str = Field(..., description="テナントID")
    system_prompt: str | None = Field(None, description="システムプロンプト")
    model_id: str | None = Field(None, description="デフォルトモデルID")


class TenantUpdateRequest(BaseModel):
    """テナント更新リクエスト"""

    system_prompt: str | None = Field(None, description="システムプロンプト")
    model_id: str | None = Field(None, description="デフォルトモデルID")
    status: str | None = Field(None, pattern="^(active|inactive)$", description="ステータス")


class TenantResponse(BaseModel):
    """テナントレスポンス"""

    tenant_id: str
    system_prompt: str | None = None
    model_id: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
