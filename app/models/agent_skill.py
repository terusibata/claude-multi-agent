"""
Agent Skills定義テーブル
テナントごとのAgent Skillsメタデータを管理
ファイル自体は /skills/tenant_{tenant_id}/.claude/skills/ に保存
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentSkill(Base):
    """
    Agent Skills定義テーブル
    SKILL.mdで定義されるClaudeの特殊能力パッケージのメタデータを管理
    """
    __tablename__ = "agent_skills"

    # 内部管理ID（UUID）
    skill_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # テナントID
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Skill名（ディレクトリ名と一致）
    # 例: servicenow-operations
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 表示タイトル
    display_title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # 説明
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # バージョン番号
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ファイルシステム上のパス
    # 例: /skills/tenant_xxx/.claude/skills/servicenow-operations
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # ステータス (active / inactive)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<AgentSkill(skill_id={self.skill_id}, name={self.name})>"
