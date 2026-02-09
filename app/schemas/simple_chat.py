"""
シンプルチャットスキーマ
SDKを使わない直接Bedrock呼び出しによるチャット用
"""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ===========================================
# リクエストスキーマ
# ===========================================


class SimpleChatStreamRequest(BaseModel):
    """
    シンプルチャットストリームリクエスト

    chat_idがある場合は継続、ない場合は新規作成として処理。
    新規作成時はuser_id, application_type, system_prompt, model_idが必須。
    """

    # 継続時に必要（指定がなければ新規作成）
    chat_id: str | None = Field(None, description="チャットID（継続時に指定）")

    # 新規作成時に必要
    user_id: str | None = Field(None, description="ユーザーID（新規作成時に必須）")
    application_type: str | None = Field(
        None,
        description="アプリケーションタイプ（新規作成時に必須）",
        examples=["translationApp", "summarizer", "chatbot"],
    )
    system_prompt: str | None = Field(None, description="システムプロンプト（新規作成時に必須）")
    model_id: str | None = Field(None, description="モデルID（新規作成時に必須）")

    # 常に必要
    message: str = Field(..., description="ユーザーメッセージ")


class SimpleChatListQuery(BaseModel):
    """シンプルチャット一覧クエリ"""

    user_id: str | None = Field(None, description="ユーザーIDでフィルタ")
    application_type: str | None = Field(None, description="アプリケーションタイプでフィルタ")
    status: str | None = Field(None, description="ステータスでフィルタ")
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


# ===========================================
# レスポンススキーマ
# ===========================================


class SimpleChatMessageResponse(BaseModel):
    """メッセージレスポンス"""

    message_id: str
    chat_id: str
    message_seq: int
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SimpleChatResponse(BaseModel):
    """シンプルチャットレスポンス"""

    chat_id: str
    tenant_id: str
    user_id: str
    model_id: str
    application_type: str
    system_prompt: str
    title: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SimpleChatDetailResponse(BaseModel):
    """シンプルチャット詳細レスポンス（メッセージ履歴含む）"""

    chat_id: str
    tenant_id: str
    user_id: str
    model_id: str
    application_type: str
    system_prompt: str
    title: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    messages: list[SimpleChatMessageResponse] = []

    model_config = ConfigDict(from_attributes=True)


class SimpleChatListResponse(BaseModel):
    """シンプルチャット一覧レスポンス"""

    items: list[SimpleChatResponse]
    total: int
    limit: int
    offset: int


# ===========================================
# ストリーミングイベントスキーマ
# ===========================================


class SimpleChatUsageInfo(BaseModel):
    """トークン使用情報"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class TextDeltaEvent(BaseModel):
    """テキスト増分イベント"""

    seq: int
    timestamp: str
    event_type: str = "text_delta"
    content: str


class DoneEvent(BaseModel):
    """完了イベント"""

    seq: int
    timestamp: str
    event_type: str = "done"
    title: str | None = None  # 初回のみ
    usage: SimpleChatUsageInfo
    cost_usd: Decimal


class ErrorEvent(BaseModel):
    """エラーイベント"""

    seq: int
    timestamp: str
    event_type: str = "error"
    message: str
    error_type: str
    recoverable: bool = False
