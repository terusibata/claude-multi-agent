"""
シンプルチャットスキーマ
SDKを使わない直接Bedrock呼び出しによるチャット用
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ===========================================
# リクエストスキーマ
# ===========================================


class CreateSimpleChatRequest(BaseModel):
    """シンプルチャット作成リクエスト"""

    user_id: str = Field(..., description="ユーザーID")
    application_type: str = Field(
        ...,
        description="アプリケーションタイプ（用途識別子）",
        examples=["translationApp", "summarizer", "chatbot"],
    )
    system_prompt: str = Field(..., description="システムプロンプト")
    model_id: str = Field(..., description="Bedrock モデルID")
    message: str = Field(..., description="最初のユーザーメッセージ")


class SendMessageRequest(BaseModel):
    """メッセージ送信リクエスト"""

    message: str = Field(..., description="ユーザーメッセージ")


class SimpleChatListQuery(BaseModel):
    """シンプルチャット一覧クエリ"""

    user_id: Optional[str] = Field(None, description="ユーザーIDでフィルタ")
    application_type: Optional[str] = Field(None, description="アプリケーションタイプでフィルタ")
    status: Optional[str] = Field(None, description="ステータスでフィルタ")
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
    title: Optional[str] = None
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
    title: Optional[str] = None
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
    title: Optional[str] = None  # 初回のみ
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
