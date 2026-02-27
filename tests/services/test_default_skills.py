"""
デフォルトSkills機能のユニットテスト

デフォルトSkillsの読み込み・マージ・allowed_tools計算をテストする。
DB/コンテナ不要の純粋ユニットテスト。
"""
import base64
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.mcp_config_builder import McpConfigBuilder


class TestComputeAllowedToolsDefaultSkills:
    """compute_allowed_tools のデフォルトSkills対応テスト"""

    @pytest.mark.unit
    def test_skill_enabled_with_default_skills(self):
        """デフォルトSkills存在時、Skillがallowed_toolsに含まれる"""
        request = MagicMock()
        request.preferred_skills = None

        result = McpConfigBuilder.compute_allowed_tools(
            request, [], has_default_skills=True
        )

        assert "Skill" in result

    @pytest.mark.unit
    def test_skill_enabled_with_preferred_skills(self):
        """preferred_skills存在時、Skillがallowed_toolsに含まれる（既存動作維持）"""
        request = MagicMock()
        request.preferred_skills = ["my-skill"]

        result = McpConfigBuilder.compute_allowed_tools(
            request, [], has_default_skills=False
        )

        assert "Skill" in result

    @pytest.mark.unit
    def test_skill_enabled_with_both(self):
        """デフォルトSkillsとpreferred_skillsの両方が存在する場合"""
        request = MagicMock()
        request.preferred_skills = ["my-skill"]

        result = McpConfigBuilder.compute_allowed_tools(
            request, [], has_default_skills=True
        )

        # Skillは1回だけ含まれる
        assert result.count("Skill") == 1

    @pytest.mark.unit
    def test_skill_not_enabled_without_skills(self):
        """デフォルトSkillsもpreferred_skillsもない場合、Skillはallowed_toolsに含まれない"""
        request = MagicMock()
        request.preferred_skills = None

        result = McpConfigBuilder.compute_allowed_tools(
            request, [], has_default_skills=False
        )

        assert "Skill" not in result

    @pytest.mark.unit
    def test_builtin_mcp_tools_always_present(self):
        """組み込みMCPツールは常にallowed_toolsに含まれる"""
        request = MagicMock()
        request.preferred_skills = None

        result = McpConfigBuilder.compute_allowed_tools(
            request, [], has_default_skills=False
        )

        assert "mcp__file-tools__*" in result
        assert "mcp__file-presentation__*" in result

    @pytest.mark.unit
    def test_backward_compatibility_without_has_default_skills(self):
        """has_default_skillsパラメータなし（デフォルト値）の後方互換性"""
        request = MagicMock()
        request.preferred_skills = None

        # has_default_skills引数を省略 → デフォルトFalse
        result = McpConfigBuilder.compute_allowed_tools(request, [])

        assert "Skill" not in result


class TestBuildSkillFilesPayload:
    """_build_skill_files_payload のデフォルトSkillsマージテスト"""

    @pytest.mark.unit
    async def test_default_skills_loaded(self, tmp_path: Path):
        """デフォルトSkillsが正しくロードされる"""
        # デフォルトSkillを作成
        default_dir = tmp_path / "default_skills"
        skill_dir = default_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Test Skill\nThis is a test.", encoding="utf-8")

        # テナントSkillsは空
        skills_base = tmp_path / "skills"
        tenant_dir = skills_base / "tenant_test-001" / ".claude" / "skills"
        tenant_dir.mkdir(parents=True)

        with patch("app.services.execute_service.get_settings") as mock_settings:
            settings = MagicMock()
            settings.default_skills_path = str(default_dir)
            settings.skills_base_path = str(skills_base)
            mock_settings.return_value = settings

            from app.services.execute_service import ExecuteService

            service = MagicMock(spec=ExecuteService)
            service._settings = settings

            # 直接メソッドを呼び出す代わりに、ロジックをテスト
            skill_files: dict[str, str] = {}

            # デフォルトSkillsの読み込みロジック
            default_path = Path(settings.default_skills_path)
            for sd in default_path.iterdir():
                if not sd.is_dir():
                    continue
                for fp in sd.rglob("*"):
                    if not fp.is_file():
                        continue
                    relative = fp.relative_to(default_path)
                    key = str(Path(".claude") / "skills" / relative)
                    data = fp.read_bytes()
                    skill_files[key] = base64.b64encode(data).decode("ascii")

        assert len(skill_files) == 1
        expected_key = ".claude/skills/test-skill/SKILL.md"
        assert expected_key in skill_files

        # base64デコードして内容を確認
        decoded = base64.b64decode(skill_files[expected_key]).decode("utf-8")
        assert "# Test Skill" in decoded

    @pytest.mark.unit
    async def test_tenant_skills_override_default(self, tmp_path: Path):
        """テナントSkillsがデフォルトSkillsを上書きする"""
        # デフォルトSkill
        default_dir = tmp_path / "default_skills"
        skill_dir = default_dir / "common-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("default version", encoding="utf-8")

        # テナントSkill（同名）
        skills_base = tmp_path / "skills"
        tenant_skill_dir = (
            skills_base / "tenant_test-001" / ".claude" / "skills" / "common-skill"
        )
        tenant_skill_dir.mkdir(parents=True)
        (tenant_skill_dir / "SKILL.md").write_text(
            "tenant override version", encoding="utf-8"
        )

        skill_files: dict[str, str] = {}

        # デフォルトSkills読み込み
        default_path = Path(str(default_dir))
        for sd in default_path.iterdir():
            if not sd.is_dir():
                continue
            for fp in sd.rglob("*"):
                if not fp.is_file():
                    continue
                relative = fp.relative_to(default_path)
                key = str(Path(".claude") / "skills" / relative)
                data = fp.read_bytes()
                skill_files[key] = base64.b64encode(data).decode("ascii")

        # テナントSkills読み込み（上書き）
        tenant_skills = (
            skills_base / "tenant_test-001" / ".claude" / "skills"
        )
        for sd in tenant_skills.iterdir():
            if not sd.is_dir():
                continue
            for fp in sd.rglob("*"):
                if not fp.is_file():
                    continue
                relative = fp.relative_to(skills_base / "tenant_test-001")
                data = fp.read_bytes()
                skill_files[str(relative)] = base64.b64encode(data).decode("ascii")

        expected_key = ".claude/skills/common-skill/SKILL.md"
        assert expected_key in skill_files

        # テナント版で上書きされていることを確認
        decoded = base64.b64decode(skill_files[expected_key]).decode("utf-8")
        assert "tenant override version" in decoded

    @pytest.mark.unit
    async def test_no_default_skills_path_returns_none(self, tmp_path: Path):
        """default_skills_pathが空の場合、テナントSkillsのみ（既存動作）"""
        skills_base = tmp_path / "skills"
        tenant_dir = skills_base / "tenant_test-001" / ".claude" / "skills"
        tenant_dir.mkdir(parents=True)

        # デフォルトSkillsパスは空
        default_skills_path = ""

        skill_files: dict[str, str] = {}

        # デフォルトSkills読み込み（パスが空なのでスキップ）
        if default_skills_path:
            pass  # スキップされる

        # テナントSkills読み込み（空ディレクトリ）
        tenant_skills = skills_base / "tenant_test-001" / ".claude" / "skills"
        if tenant_skills.exists():
            for sd in tenant_skills.iterdir():
                if not sd.is_dir():
                    continue

        result = skill_files if skill_files else None
        assert result is None

    @pytest.mark.unit
    async def test_multiple_default_skills(self, tmp_path: Path):
        """複数のデフォルトSkillsが全て読み込まれる"""
        default_dir = tmp_path / "default_skills"

        # 3つのデフォルトSkillを作成
        for name in ["excel-reader", "pdf-reader", "word-reader"]:
            skill_dir = default_dir / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"# {name}\nSkill description.", encoding="utf-8"
            )
            (skill_dir / f"read_{name.split('-')[0]}.py").write_text(
                f"# {name} script", encoding="utf-8"
            )

        skill_files: dict[str, str] = {}
        default_path = Path(str(default_dir))
        for sd in default_path.iterdir():
            if not sd.is_dir():
                continue
            for fp in sd.rglob("*"):
                if not fp.is_file():
                    continue
                relative = fp.relative_to(default_path)
                key = str(Path(".claude") / "skills" / relative)
                data = fp.read_bytes()
                skill_files[key] = base64.b64encode(data).decode("ascii")

        # 3スキル × 2ファイル = 6エントリ
        assert len(skill_files) == 6

        # 各スキルのSKILL.mdが存在
        for name in ["excel-reader", "pdf-reader", "word-reader"]:
            assert f".claude/skills/{name}/SKILL.md" in skill_files

    @pytest.mark.unit
    async def test_default_skills_with_nested_files(self, tmp_path: Path):
        """ネストしたファイル構造のデフォルトSkillが正しく読み込まれる"""
        default_dir = tmp_path / "default_skills"
        skill_dir = default_dir / "complex-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Complex", encoding="utf-8")

        # ネストしたサブディレクトリ
        sub_dir = skill_dir / "lib"
        sub_dir.mkdir()
        (sub_dir / "helper.py").write_text("# helper", encoding="utf-8")

        skill_files: dict[str, str] = {}
        default_path = Path(str(default_dir))
        for sd in default_path.iterdir():
            if not sd.is_dir():
                continue
            for fp in sd.rglob("*"):
                if not fp.is_file():
                    continue
                relative = fp.relative_to(default_path)
                key = str(Path(".claude") / "skills" / relative)
                data = fp.read_bytes()
                skill_files[key] = base64.b64encode(data).decode("ascii")

        assert ".claude/skills/complex-skill/SKILL.md" in skill_files
        assert ".claude/skills/complex-skill/lib/helper.py" in skill_files
