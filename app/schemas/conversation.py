"""
会話・履歴スキーマ
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ConversationResponse(BaseModel):
    """会話レスポンス"""

    conversation_id: str
    session_id: Optional[str] = None
    tenant_id: str
    user_id: str
    model_id: str
    title: Optional[str] = None
    status: str
    workspace_enabled: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageLogResponse(BaseModel):
    """メッセージログレスポンス"""

    message_id: str
    conversation_id: str
    message_seq: int
    message_type: str
    message_subtype: Optional[str] = None
    content: Optional[dict[str, Any]] = None
    timestamp: datetime

    class Config:
        from_attributes = True


class ConversationListQuery(BaseModel):
    """会話一覧クエリ"""

    user_id: Optional[str] = None
    status: Optional[str] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ConversationArchiveRequest(BaseModel):
    """会話アーカイブリクエスト"""

    pass


class ConversationUpdateRequest(BaseModel):
    """会話更新リクエスト"""

    title: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = Field(None, pattern="^(active|archived)$")


class ConversationCreateRequest(BaseModel):
    """会話作成リクエスト"""

    user_id: str = Field(..., description="ユーザーID")
    model_id: Optional[str] = Field(None, description="モデルID（省略時はテナントのデフォルト）")
    workspace_enabled: bool = Field(default=False, description="ワークスペースを有効にするか")
