"""
Agent Skillsサービス
ファイルシステムベースのSkills管理
"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.agent_skill import AgentSkill
from app.schemas.skill import SkillCreate, SkillFileInfo, SkillUpdate

settings = get_settings()


class SkillService:
    """Agent Skillsサービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db
        self.base_path = Path(settings.skills_base_path)

    def _get_tenant_skills_path(self, tenant_id: str) -> Path:
        """
        テナントのSkillsディレクトリパスを取得

        Args:
            tenant_id: テナントID

        Returns:
            Skillsディレクトリパス
        """
        return self.base_path / f"tenant_{tenant_id}" / ".claude" / "skills"

    def _get_skill_path(self, tenant_id: str, skill_name: str) -> Path:
        """
        Skillのディレクトリパスを取得

        Args:
            tenant_id: テナントID
            skill_name: Skill名

        Returns:
            Skillディレクトリパス
        """
        return self._get_tenant_skills_path(tenant_id) / skill_name

    async def get_all_by_tenant(
        self,
        tenant_id: str,
        status: Optional[str] = None,
    ) -> list[AgentSkill]:
        """
        テナントの全Skillsを取得

        Args:
            tenant_id: テナントID
            status: フィルタリング用ステータス

        Returns:
            Skillsリスト
        """
        query = select(AgentSkill).where(AgentSkill.tenant_id == tenant_id)
        if status:
            query = query.where(AgentSkill.status == status)
        query = query.order_by(AgentSkill.name)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_id(
        self,
        skill_id: str,
        tenant_id: str,
    ) -> Optional[AgentSkill]:
        """
        IDでSkillを取得

        Args:
            skill_id: Skill ID
            tenant_id: テナントID

        Returns:
            Skill（存在しない場合はNone）
        """
        query = select(AgentSkill).where(
            AgentSkill.skill_id == skill_id,
            AgentSkill.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_name(
        self,
        name: str,
        tenant_id: str,
    ) -> Optional[AgentSkill]:
        """
        名前でSkillを取得

        Args:
            name: Skill名
            tenant_id: テナントID

        Returns:
            Skill（存在しない場合はNone）
        """
        query = select(AgentSkill).where(
            AgentSkill.name == name,
            AgentSkill.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create(
        self,
        tenant_id: str,
        skill_data: SkillCreate,
        files: dict[str, str],
    ) -> AgentSkill:
        """
        Skillを作成

        Args:
            tenant_id: テナントID
            skill_data: 作成データ
            files: アップロードファイル {"filename": "content", ...}

        Returns:
            作成されたSkill
        """
        # ファイルシステムにディレクトリ作成
        skill_path = self._get_skill_path(tenant_id, skill_data.name)
        skill_path.mkdir(parents=True, exist_ok=True)

        # ファイルを保存
        for filename, content in files.items():
            file_path = skill_path / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        # DBにメタデータを保存
        skill = AgentSkill(
            skill_id=str(uuid4()),
            tenant_id=tenant_id,
            name=skill_data.name,
            display_title=skill_data.display_title,
            description=skill_data.description,
            slash_command=skill_data.slash_command,
            slash_command_description=skill_data.slash_command_description,
            is_user_selectable=skill_data.is_user_selectable,
            version=1,
            file_path=str(skill_path),
            status="active",
        )
        self.db.add(skill)
        await self.db.flush()
        await self.db.refresh(skill)
        return skill

    async def update(
        self,
        skill_id: str,
        tenant_id: str,
        skill_data: SkillUpdate,
    ) -> Optional[AgentSkill]:
        """
        Skillメタデータを更新

        Args:
            skill_id: Skill ID
            tenant_id: テナントID
            skill_data: 更新データ

        Returns:
            更新されたSkill（存在しない場合はNone）
        """
        skill = await self.get_by_id(skill_id, tenant_id)
        if not skill:
            return None

        update_data = skill_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(skill, field, value)

        await self.db.flush()
        await self.db.refresh(skill)
        return skill

    async def update_files(
        self,
        skill_id: str,
        tenant_id: str,
        files: dict[str, str],
    ) -> Optional[AgentSkill]:
        """
        Skillファイルを更新

        Args:
            skill_id: Skill ID
            tenant_id: テナントID
            files: 更新ファイル {"filename": "content", ...}

        Returns:
            更新されたSkill（存在しない場合はNone）
        """
        skill = await self.get_by_id(skill_id, tenant_id)
        if not skill:
            return None

        skill_path = Path(skill.file_path)

        # ファイルを保存
        for filename, content in files.items():
            file_path = skill_path / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        # バージョンを更新
        skill.version += 1
        await self.db.flush()
        await self.db.refresh(skill)
        return skill

    async def delete(
        self,
        skill_id: str,
        tenant_id: str,
    ) -> bool:
        """
        Skillを削除

        Args:
            skill_id: Skill ID
            tenant_id: テナントID

        Returns:
            削除成功かどうか
        """
        skill = await self.get_by_id(skill_id, tenant_id)
        if not skill:
            return False

        # ファイルシステムから削除
        skill_path = Path(skill.file_path)
        if skill_path.exists():
            shutil.rmtree(skill_path)

        # DBから削除
        await self.db.delete(skill)
        return True

    async def get_files(
        self,
        skill_id: str,
        tenant_id: str,
    ) -> Optional[list[SkillFileInfo]]:
        """
        Skillのファイル一覧を取得

        Args:
            skill_id: Skill ID
            tenant_id: テナントID

        Returns:
            ファイル情報リスト（存在しない場合はNone）
        """
        skill = await self.get_by_id(skill_id, tenant_id)
        if not skill:
            return None

        skill_path = Path(skill.file_path)
        if not skill_path.exists():
            return []

        files = []
        for file_path in skill_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append(
                    SkillFileInfo(
                        filename=file_path.name,
                        path=str(file_path.relative_to(skill_path)),
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime),
                    )
                )
        return files

    async def get_file_content(
        self,
        skill_id: str,
        tenant_id: str,
        file_path: str,
    ) -> Optional[str]:
        """
        Skillファイルの内容を取得

        Args:
            skill_id: Skill ID
            tenant_id: テナントID
            file_path: ファイルパス（Skillディレクトリからの相対パス）

        Returns:
            ファイル内容（存在しない場合はNone）
        """
        skill = await self.get_by_id(skill_id, tenant_id)
        if not skill:
            return None

        full_path = Path(skill.file_path) / file_path
        if not full_path.exists() or not full_path.is_file():
            return None

        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def get_tenant_cwd(self, tenant_id: str) -> str:
        """
        テナント専用のcwd（作業ディレクトリ）を取得
        ディレクトリが存在しない場合は自動的に作成し、
        Claude Agent SDKが期待する.claude/skills/構造も初期化します。

        Args:
            tenant_id: テナントID

        Returns:
            cwdパス
        """
        tenant_path = self.base_path / f"tenant_{tenant_id}"
        # ディレクトリが存在しない場合は作成
        tenant_path.mkdir(parents=True, exist_ok=True)

        # .claude/skills/ ディレクトリ構造を作成
        # Claude Agent SDKが setting_sources=["project"] を使用する場合に必要
        claude_dir = tenant_path / ".claude"
        skills_dir = claude_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        return str(tenant_path)

    async def get_slash_commands(
        self,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        """
        ユーザーが選択可能なスラッシュコマンド一覧を取得

        Args:
            tenant_id: テナントID

        Returns:
            スラッシュコマンドアイテムのリスト
        """
        query = select(AgentSkill).where(
            AgentSkill.tenant_id == tenant_id,
            AgentSkill.status == "active",
            AgentSkill.is_user_selectable == True,
            AgentSkill.slash_command.isnot(None),
        ).order_by(AgentSkill.slash_command)

        result = await self.db.execute(query)
        skills = result.scalars().all()

        return [
            {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "slash_command": skill.slash_command,
                "description": skill.slash_command_description,
            }
            for skill in skills
        ]
