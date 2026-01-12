"""
MCPサーバー定義スキーマ
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class McpToolInputSchema(BaseModel):
    """MCPツールの入力スキーマ"""

    type: str = Field(default="object", description="JSONスキーマのタイプ")
    properties: dict[str, Any] = Field(default_factory=dict, description="プロパティ定義")
    required: Optional[list[str]] = Field(None, description="必須プロパティ")


class McpToolDefinition(BaseModel):
    """MCPツール定義"""

    name: str = Field(..., description="ツール名")
    description: str = Field(..., description="ツールの説明")
    input_schema: McpToolInputSchema = Field(..., description="入力スキーマ")


class McpServerBase(BaseModel):
    """MCPサーバー定義の共通フィールド"""

    name: str = Field(..., description="MCPサーバー名（識別子）", max_length=200)
    display_name: Optional[str] = Field(None, description="表示名", max_length=300)
    type: str = Field(..., description="タイプ (http / sse / stdio / builtin)")
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
    tools: Optional[list[McpToolDefinition]] = Field(
        None, description="ツール定義リスト（builtinタイプの場合）"
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
    tools: Optional[list[McpToolDefinition]] = None
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")


class McpServerResponse(McpServerBase):
    """MCPサーバーレスポンス"""

    mcp_server_id: str
    tenant_id: str
    tools: Optional[list[McpToolDefinition]] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
