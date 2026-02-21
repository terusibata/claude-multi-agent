"""
ワークスペースエージェント リクエスト/レスポンスモデル
ホスト側Backendと通信するためのスキーマ定義（UDS / HTTP両対応）
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


class ExecRequest(BaseModel):
    """コンテナ内コマンド実行リクエスト（ECSモード用）"""

    cmd: list[str]
    timeout: int = 60


class ExecResponse(BaseModel):
    """コンテナ内コマンド実行レスポンス"""

    exit_code: int
    output: str


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス"""

    status: str = "ok"
