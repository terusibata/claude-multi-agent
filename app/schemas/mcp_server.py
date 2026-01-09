"""
MCPサーバー定義スキーマ
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class McpServerBase(BaseModel):
    """MCPサーバー定義の共通フィールド"""

    name: str = Field(..., description="MCPサーバー名（識別子）", max_length=200)
    display_name: Optional[str] = Field(None, description="表示名", max_length=300)
    type: str = Field(..., description="タイプ (http / sse / stdio)")
    url: Optional[str] = Field(
        None, description="サーバーURL（http/sseの場合）", max_length=500
    )
    command: Optional[str] = Field(
        None, description="起動コマンド（stdioの場合）", max_length=500
    )
    args: Optional[list[str]] = Field(None, description="コマンド引数（stdioの場合）")
    env: Optional[dict[str, str]] = Field(None, description="環境変数")
    headers_template: Optional[dict[str, str]] = Field(
        None,
        description="ヘッダーテンプレート（例: {'Authorization': 'Bearer ${token}'}）",
    )
    allowed_tools: Optional[list[str]] = Field(
        None, description="許可するツール名のリスト"
    )
    description: Optional[str] = Field(None, description="説明")


class McpServerCreate(McpServerBase):
    """MCPサーバー作成リクエスト"""

    pass


class McpServerUpdate(BaseModel):
    """MCPサーバー更新リクエスト"""

    display_name: Optional[str] = Field(None, max_length=300)
    type: Optional[str] = None
    url: Optional[str] = Field(None, max_length=500)
    command: Optional[str] = Field(None, max_length=500)
    args: Optional[list[str]] = None
    env: Optional[dict[str, str]] = None
    headers_template: Optional[dict[str, str]] = None
    allowed_tools: Optional[list[str]] = None
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")


class McpServerResponse(McpServerBase):
    """MCPサーバーレスポンス"""

    mcp_server_id: str
    tenant_id: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
