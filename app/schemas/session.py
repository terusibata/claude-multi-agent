"""
セッション・履歴スキーマ
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatSessionResponse(BaseModel):
    """チャットセッションレスポンス"""

    chat_session_id: str
    session_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    tenant_id: str
    user_id: str
    agent_config_id: Optional[str] = None
    title: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageLogResponse(BaseModel):
    """メッセージログレスポンス"""

    message_id: str
    chat_session_id: str
    message_seq: int
    message_type: str
    message_subtype: Optional[str] = None
    content: Optional[dict[str, Any]] = None
    timestamp: datetime

    class Config:
        from_attributes = True


class SessionListQuery(BaseModel):
    """セッション一覧クエリ"""

    user_id: Optional[str] = None
    status: Optional[str] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SessionArchiveRequest(BaseModel):
    """セッションアーカイブリクエスト"""

    pass


class SessionUpdateRequest(BaseModel):
    """セッション更新リクエスト"""

    title: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = Field(None, pattern="^(active|archived)$")
