"""
ワークスペースエージェント リクエスト/レスポンスモデル
AgentCore Runtime の /invocations エンドポイント向けスキーマ定義
"""
from pydantic import BaseModel, Field


class WorkspaceSyncConfig(BaseModel):
    """S3ワークスペース同期設定"""

    enabled: bool = False
    s3_bucket: str = ""
    s3_prefix: str = "workspaces/"
    tenant_id: str = ""
    conversation_id: str = ""


class InvocationRequest(BaseModel):
    """AgentCore /invocations リクエスト

    AgentCoreの /invocations エンドポイントは単一のPOSTで全情報を受け取る。
    """

    user_input: str
    system_prompt: str = ""
    model: str = "claude-sonnet-4-5-20250929"
    session_id: str | None = None
    max_turns: int | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    cwd: str = "/workspace"
    setting_sources: list[str] | None = None
    mcp_server_configs: list[dict] | None = None

    # AgentCore 固有フィールド
    workspace_sync: WorkspaceSyncConfig | None = None
    skill_files: dict[str, str] | None = None  # base64エンコードされたスキルファイル群
    aws_region: str = "us-west-2"
    bedrock_model_id: str = ""


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス"""

    status: str = "ok"
