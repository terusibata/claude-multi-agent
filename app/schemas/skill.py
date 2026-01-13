"""
Agent Skills スキーマ
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SkillBase(BaseModel):
    """Agent Skillsの共通フィールド"""

    name: str = Field(
        ...,
        description="Skill名（ディレクトリ名と一致）",
        min_length=1,
        max_length=200,
        pattern=r"^[a-zA-Z0-9_\-]+$",
    )
    display_title: Optional[str] = Field(None, description="表示タイトル", max_length=300)
    description: Optional[str] = Field(None, description="説明")
    # スラッシュコマンド設定
    slash_command: Optional[str] = Field(
        None,
        description="スラッシュコマンド表示名（例: /ServiceNowドキュメント検索）",
        max_length=100,
    )
    slash_command_description: Optional[str] = Field(
        None,
        description="スラッシュコマンドの説明（オートコンプリート時に表示）",
        max_length=500,
    )
    is_user_selectable: bool = Field(
        default=True,
        description="ユーザーがUIから選択可能かどうか",
    )

    @field_validator("slash_command")
    @classmethod
    def validate_slash_command(cls, v: Optional[str]) -> Optional[str]:
        """スラッシュコマンドは'/'で始まる必要がある"""
        if v is None:
            return v
        if not v.startswith("/"):
            raise ValueError("スラッシュコマンドは '/' で始める必要があります")
        return v


class SkillCreate(SkillBase):
    """Agent Skills作成リクエスト"""

    pass


class SkillUpdate(BaseModel):
    """Agent Skills更新リクエスト"""

    display_title: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")
    # スラッシュコマンド設定
    slash_command: Optional[str] = Field(None, max_length=100)
    slash_command_description: Optional[str] = Field(None, max_length=500)
    is_user_selectable: Optional[bool] = None

    @field_validator("slash_command")
    @classmethod
    def validate_slash_command(cls, v: Optional[str]) -> Optional[str]:
        """スラッシュコマンドは'/'で始まる必要がある"""
        if v is None:
            return v
        if not v.startswith("/"):
            raise ValueError("スラッシュコマンドは '/' で始める必要があります")
        return v


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


class SlashCommandItem(BaseModel):
    """スラッシュコマンドアイテム（オートコンプリート用）"""

    skill_id: str = Field(..., description="SkillのID")
    name: str = Field(..., description="Skill名（preferred_skillsに渡す値）")
    slash_command: str = Field(..., description="スラッシュコマンド表示名")
    description: Optional[str] = Field(None, description="説明")


class SlashCommandListResponse(BaseModel):
    """スラッシュコマンド一覧レスポンス"""

    items: list[SlashCommandItem] = Field(
        default_factory=list,
        description="スラッシュコマンドアイテムリスト",
    )
