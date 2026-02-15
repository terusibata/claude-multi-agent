"""
ワークスペースエージェント リクエスト/レスポンスモデル
ホスト側BackendとUnix Socket経由で通信するためのスキーマ定義
"""
from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    """エージェント実行リクエスト"""

    user_input: str
    system_prompt: str = ""
    model: str = "claude-sonnet-4-5-20250929"
    session_id: str | None = None
    max_turns: int | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    cwd: str = "/workspace"
    setting_sources: list[str] | None = None
    mcp_server_configs: list[dict] | None = None


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス"""

    status: str = "ok"
