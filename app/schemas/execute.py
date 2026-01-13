"""
エージェント実行スキーマ
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class ExecutorInfo(BaseModel):
    """実行者情報"""

    user_id: str = Field(..., description="ユーザーID")
    name: str = Field(..., description="名前")
    email: str = Field(..., description="メールアドレス")
    employee_id: Optional[str] = Field(None, description="社員番号")


class ExecuteRequest(BaseModel):
    """エージェント実行リクエスト"""

    # 必須パラメータ
    agent_config_id: str = Field(..., description="エージェント実行設定ID")
    model_id: str = Field(..., description="モデルID")
    chat_session_id: Optional[str] = Field(
        None, description="セッションID（省略時は新規作成、指定時は継続）"
    )
    user_input: str = Field(..., description="ユーザー入力")
    executor: ExecutorInfo = Field(..., description="実行者情報")

    # MCPサーバー用認証情報（一時トークン）
    tokens: Optional[dict[str, str]] = Field(
        None,
        description="MCPサーバー用認証情報（例: {'servicenowToken': 'xxx'}）",
    )

    # 任意パラメータ
    resume_session_id: Optional[str] = Field(
        None, description="継続するSDKセッションID"
    )
    fork_session: bool = Field(default=False, description="セッションをフォークするか")

    # ワークスペース設定
    enable_workspace: bool = Field(
        default=False,
        description="セッション専用ワークスペースを有効にする",
    )

    @model_validator(mode="after")
    def ensure_chat_session_id(self) -> "ExecuteRequest":
        """chat_session_idがNoneまたは空文字列の場合は新しいUUIDを生成"""
        if not self.chat_session_id or (
            isinstance(self.chat_session_id, str) and self.chat_session_id.strip() == ""
        ):
            self.chat_session_id = str(uuid4())
        return self


class UsageInfo(BaseModel):
    """トークン使用情報"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0


class SSEEvent(BaseModel):
    """SSEイベント"""

    event: str = Field(
        ...,
        description="イベントタイプ (session_start / text_delta / tool_use / tool_result / thinking / result)",
    )
    data: dict[str, Any] = Field(..., description="イベントデータ")


class SessionStartData(BaseModel):
    """セッション開始イベントデータ"""

    session_id: str
    tools: list[str]
    model: str


class TextDeltaData(BaseModel):
    """テキスト増分イベントデータ"""

    text: str


class ToolUseData(BaseModel):
    """ツール使用開始イベントデータ"""

    tool_use_id: str
    tool_name: str
    summary: str


class ToolResultData(BaseModel):
    """ツール結果イベントデータ"""

    tool_use_id: str
    tool_name: str
    status: str
    summary: str


class ThinkingData(BaseModel):
    """思考プロセスイベントデータ"""

    content: str


class ResultData(BaseModel):
    """結果イベントデータ"""

    subtype: str  # success / error_during_execution
    result: Optional[str] = None
    errors: Optional[list[str]] = None
    usage: UsageInfo
    cost_usd: Decimal
    num_turns: int
    duration_ms: int


class ExecuteResponse(BaseModel):
    """エージェント実行レスポンス（非ストリーミング用）"""

    chat_session_id: str
    session_id: str
    result: Optional[str]
    usage: UsageInfo
    cost_usd: Decimal
    num_turns: int
    duration_ms: int
    created_at: datetime
