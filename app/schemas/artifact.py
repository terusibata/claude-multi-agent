"""
アーティファクトスキーマ
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ArtifactResponse(BaseModel):
    """アーティファクトレスポンススキーマ"""

    artifact_id: str = Field(..., description="アーティファクトID")
    chat_session_id: str = Field(..., description="チャットセッションID")
    turn_number: int = Field(..., description="ターン番号")
    artifact_type: str = Field(..., description="アーティファクトタイプ (file/code/notebook/image/document)")
    filename: str = Field(..., description="ファイル名")
    file_path: Optional[str] = Field(None, description="ローカルファイルパス")
    s3_key: Optional[str] = Field(None, description="S3キー")
    content: Optional[str] = Field(None, description="ファイル内容（小さいファイルのみ）")
    mime_type: Optional[str] = Field(None, description="MIMEタイプ")
    file_size: Optional[int] = Field(None, description="ファイルサイズ（バイト）")
    tool_name: str = Field(..., description="ツール名 (Write/NotebookEdit)")
    title: Optional[str] = Field(None, description="タイトル")
    description: Optional[str] = Field(None, description="説明")
    created_at: datetime = Field(..., description="作成日時")

    model_config = {"from_attributes": True}


class ArtifactListResponse(BaseModel):
    """アーティファクトリストレスポンススキーマ"""

    artifacts: list[ArtifactResponse] = Field(..., description="アーティファクトリスト")
    total_count: int = Field(..., description="総件数")
