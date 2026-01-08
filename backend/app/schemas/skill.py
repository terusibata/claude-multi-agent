"""
Agent Skills スキーマ
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SkillBase(BaseModel):
    """Agent Skillsの共通フィールド"""

    name: str = Field(
        ..., description="Skill名（ディレクトリ名と一致）", max_length=200
    )
    display_title: Optional[str] = Field(None, description="表示タイトル", max_length=300)
    description: Optional[str] = Field(None, description="説明")


class SkillCreate(SkillBase):
    """Agent Skills作成リクエスト"""

    pass


class SkillUpdate(BaseModel):
    """Agent Skills更新リクエスト"""

    display_title: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")


class SkillResponse(SkillBase):
    """Agent Skillsレスポンス"""

    skill_id: str
    tenant_id: str
    version: int
    file_path: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SkillFileInfo(BaseModel):
    """Skillファイル情報"""

    filename: str
    path: str
    size: int
    modified_at: datetime


class SkillFilesResponse(BaseModel):
    """Skillファイル一覧レスポンス"""

    skill_id: str
    skill_name: str
    files: list[SkillFileInfo]
