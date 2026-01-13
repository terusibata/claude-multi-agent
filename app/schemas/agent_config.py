"""
エージェント実行設定スキーマ
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AgentConfigBase(BaseModel):
    """エージェント実行設定の共通フィールド"""

    name: str = Field(..., description="設定名", max_length=200)
    description: Optional[str] = Field(None, description="説明")
    system_prompt: Optional[str] = Field(None, description="システムプロンプト")
    model_id: str = Field(..., description="使用するモデルID")
    allowed_tools: Optional[list[str]] = Field(
        None,
        description="許可するツールのリスト（例: ['Read', 'Write', 'Bash']）",
    )
    permission_mode: str = Field(
        default="default",
        description="権限モード (default / acceptEdits / bypassPermissions / plan)",
    )
    agent_skills: Optional[list[str]] = Field(
        None, description="使用するAgent Skills名のリスト"
    )
    mcp_servers: Optional[list[str]] = Field(
        None, description="使用するMCPサーバーIDのリスト"
    )
    workspace_enabled: bool = Field(
        default=False,
        description="セッション専用ワークスペースを有効にするか",
    )


class AgentConfigCreate(AgentConfigBase):
    """エージェント実行設定作成リクエスト"""

    pass


class AgentConfigUpdate(BaseModel):
    """エージェント実行設定更新リクエスト"""

    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model_id: Optional[str] = None
    allowed_tools: Optional[list[str]] = None
    permission_mode: Optional[str] = None
    agent_skills: Optional[list[str]] = None
    mcp_servers: Optional[list[str]] = None
    workspace_enabled: Optional[bool] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")


class AgentConfigResponse(AgentConfigBase):
    """エージェント実行設定レスポンス"""

    agent_config_id: str
    tenant_id: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
