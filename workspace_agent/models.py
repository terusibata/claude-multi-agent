"""
ワークスペースエージェント リクエスト/レスポンスモデル
ホスト側BackendとUnix Socket経由で通信するためのスキーマ定義
"""
from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    """MCP サーバー設定"""

    name: str
    type: str  # "http" | "sse" | "stdio" | "builtin" | "openapi"
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None


class ExecuteRequest(BaseModel):
    """エージェント実行リクエスト"""

    user_input: str
    system_prompt: str = ""
    model: str = "claude-sonnet-4-5-20250929"
    session_id: str | None = None
    max_iterations: int = 50
    budget_tokens: int = 200000
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    cwd: str = "/workspace"


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス"""

    status: str = "ok"
