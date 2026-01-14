"""
エージェント実行スキーマ
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field


class ExecutorInfo(BaseModel):
    """実行者情報"""

    user_id: str = Field(..., description="ユーザーID")
    name: str = Field(..., description="名前")
    email: str = Field(..., description="メールアドレス")
    employee_id: Optional[str] = Field(None, description="社員番号")


class StreamRequest(BaseModel):
    """
    会話ストリーミングリクエスト

    /conversations/{conversation_id}/stream エンドポイント用。
    conversation_idはURLパスから取得するため、このスキーマには含まれない。
    model_id, workspace_enabledは会話レコードから取得する。
    """

    # 必須パラメータ
    user_input: str = Field(..., description="ユーザー入力")
    executor: ExecutorInfo = Field(..., description="実行者情報")

    # MCPサーバー用認証情報（一時トークン）
    tokens: Optional[dict[str, str]] = Field(
        None,
        description="MCPサーバー用認証情報（例: {'servicenowToken': 'xxx'}）",
    )

    # Skill/ツール優先指定
    preferred_skills: Optional[list[str]] = Field(
        None,
        description="優先的に使用するSkill名のリスト",
    )


class ExecuteRequest(BaseModel):
    """
    エージェント実行リクエスト（内部用）

    APIからExecuteServiceに渡される内部リクエスト。
    会話から取得した情報を含む。
    """

    # 会話情報（会話から取得）
    conversation_id: str = Field(..., description="会話ID")
    tenant_id: str = Field(..., description="テナントID")
    model_id: str = Field(..., description="モデルID")
    workspace_enabled: bool = Field(default=False, description="ワークスペース有効フラグ")

    # リクエスト情報
    user_input: str = Field(..., description="ユーザー入力")
    executor: ExecutorInfo = Field(..., description="実行者情報")

    # MCPサーバー用認証情報
    tokens: Optional[dict[str, str]] = Field(
        None,
        description="MCPサーバー用認証情報",
    )

    # Skill優先指定
    preferred_skills: Optional[list[str]] = Field(
        None,
        description="優先的に使用するSkill名のリスト",
    )


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

    conversation_id: str
    session_id: str
    result: Optional[str]
    usage: UsageInfo
    cost_usd: Decimal
    num_turns: int
    duration_ms: int
    created_at: datetime
