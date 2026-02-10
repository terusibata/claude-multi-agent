"""
MCPサーバー定義テーブル
テナントごとのMCPサーバー設定を管理
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class McpServer(Base):
    """
    MCPサーバー定義テーブル
    Model Context Protocol (MCP) サーバーの設定を管理
    """
    __tablename__ = "mcp_servers"

    # 内部管理ID
    mcp_server_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # テナントID
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # MCPサーバー名（識別子）
    # 例: servicenow, salesforce
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 表示名
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # タイプ: http / sse / stdio / builtin / openapi
    # builtinはアプリケーション組み込みのMCPサーバー
    # openapiはOpenAPI仕様からツールを動的生成するタイプ
    type: Mapped[str] = mapped_column(String(20), nullable=False)

    # サーバーURL（http/sseの場合）
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 起動コマンド（stdioの場合）
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # コマンド引数（stdioの場合）（JSON配列）
    args: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 環境変数（JSON）
    env: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ヘッダーテンプレート（JSON）
    # 例: {"Authorization": "Bearer ${servicenowToken}"}
    headers_template: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 許可するツール名のリスト（JSON配列）
    # 例: ["mcp__servicenow__create_ticket", "mcp__servicenow__get_ticket"]
    allowed_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ツール定義（JSON配列）- builtinタイプの場合に使用
    # 例: [{"name": "present_files", "description": "...", "input_schema": {...}}]
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 説明
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # OpenAPI仕様（JSON）- openapiタイプの場合に使用
    openapi_spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # OpenAPI APIのベースURL（openapiタイプの場合）
    # 仕様のserversセクションを上書き
    openapi_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

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

    def __repr__(self) -> str:
        return f"<McpServer(mcp_server_id={self.mcp_server_id}, name={self.name})>"
