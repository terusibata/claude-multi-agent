"""
テナントスキーマ
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TenantCreateRequest(BaseModel):
    """テナント作成リクエスト"""

    tenant_id: str = Field(..., description="テナントID")
    system_prompt: Optional[str] = Field(None, description="システムプロンプト")
    model_id: Optional[str] = Field(None, description="デフォルトモデルID")


class TenantUpdateRequest(BaseModel):
    """テナント更新リクエスト"""

    system_prompt: Optional[str] = Field(None, description="システムプロンプト")
    model_id: Optional[str] = Field(None, description="デフォルトモデルID")
    status: Optional[str] = Field(None, pattern="^(active|inactive)$", description="ステータス")


class TenantResponse(BaseModel):
    """テナントレスポンス"""

    tenant_id: str
    system_prompt: Optional[str] = None
    model_id: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
