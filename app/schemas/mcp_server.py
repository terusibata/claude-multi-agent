"""
MCPサーバー定義スキーマ（OpenAPI専用）
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class McpServerBase(BaseModel):
    """MCPサーバー定義の共通フィールド"""

    name: str = Field(..., description="MCPサーバー名（識別子）", max_length=200)
    display_name: str | None = Field(None, description="表示名", max_length=300)
    env: dict[str, str] | None = Field(None, description="環境変数")
    headers_template: dict[str, str] | None = Field(
        None,
        description="ヘッダーテンプレート（例: {'Authorization': 'Bearer ${token}'}）",
    )
    allowed_tools: list[str] | None = Field(
        None, description="許可するツール名のリスト"
    )
    description: str | None = Field(None, description="説明")
    openapi_spec: dict[str, Any] | None = Field(
        None,
        description="OpenAPI仕様（JSON形式）",
    )
    openapi_base_url: str | None = Field(
        None,
        description="OpenAPI APIのベースURL。仕様のserversセクションを上書き",
        max_length=500,
    )


class McpServerCreate(McpServerBase):
    """MCPサーバー作成リクエスト"""

    openapi_spec: dict[str, Any] = Field(
        ...,
        description="OpenAPI仕様（JSON形式）。必須。",
    )


class McpServerUpdate(BaseModel):
    """MCPサーバー更新リクエスト"""

    display_name: str | None = Field(None, max_length=300)
    env: dict[str, str] | None = None
    headers_template: dict[str, str] | None = None
    allowed_tools: list[str] | None = None
    description: str | None = None
    openapi_spec: dict[str, Any] | None = None
    openapi_base_url: str | None = Field(None, max_length=500)
    status: str | None = Field(None, pattern="^(active|inactive)$")


class McpServerResponse(McpServerBase):
    """MCPサーバーレスポンス"""

    mcp_server_id: str
    tenant_id: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
