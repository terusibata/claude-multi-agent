"""
ZIP Skills アップロード/ダウンロードのユニットテスト

ZIPアーカイブの検証・展開ロジックをテストする。
DB/コンテナ不要の純粋ユニットテスト。
"""
import io
import zipfile

import pytest

from app.utils.exceptions import PathTraversalError, ValidationError
from app.utils.security import validate_zip_archive


def _make_zip(files: dict[str, str]) -> bytes:
    """テスト用ZIPアーカイブを作成"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestValidateZipArchive:
    """validate_zip_archive のテスト"""

    @pytest.mark.unit
    def test_valid_zip_with_skill_md(self):
        """SKILL.mdを含む有効なZIPが正常に展開される"""
        zip_data = _make_zip({
            "SKILL.md": "# My Skill\nDescription",
            "run.py": "print('hello')",
        })
        result = validate_zip_archive(zip_data)

        assert "SKILL.md" in result
        assert "run.py" in result
        assert result["SKILL.md"] == "# My Skill\nDescription"

    @pytest.mark.unit
    def test_nested_directory_structure(self):
        """ネストしたディレクトリ構造が保持される"""
        zip_data = _make_zip({
            "SKILL.md": "# Skill",
            "lib/helper.py": "# helper",
            "lib/utils/format.py": "# format",
            "templates/report.md": "# report",
        })
        result = validate_zip_archive(zip_data)

        assert len(result) == 4
        assert "lib/helper.py" in result
        assert "lib/utils/format.py" in result
        assert "templates/report.md" in result

    @pytest.mark.unit
    def test_missing_skill_md_raises_error(self):
        """SKILL.mdがないZIPはエラー"""
        zip_data = _make_zip({
            "run.py": "print('hello')",
        })
        with pytest.raises(ValidationError):
            validate_zip_archive(zip_data)

    @pytest.mark.unit
    def test_invalid_zip_data_raises_error(self):
        """無効なZIPデータはエラー"""
        with pytest.raises(ValidationError):
            validate_zip_archive(b"not a zip file")

    @pytest.mark.unit
    def test_path_traversal_dotdot_skipped(self):
        """../で始まるパスは隠しファイルとしてスキップされる（安全な動作）"""
        zip_data = _make_zip({
            "SKILL.md": "# Skill",
            "../../../etc/passwd": "malicious",
        })
        result = validate_zip_archive(zip_data)
        # パストラバーサルファイルはスキップされ、SKILL.mdのみ残る
        assert len(result) == 1
        assert "SKILL.md" in result

    @pytest.mark.unit
    def test_path_traversal_in_subdir_rejected(self):
        """サブディレクトリ内の..はsanitize_filenameで拒否される"""
        zip_data = _make_zip({
            "SKILL.md": "# Skill",
            "lib/../../etc/passwd": "malicious",
        })
        with pytest.raises(ValidationError):
            validate_zip_archive(zip_data)

    @pytest.mark.unit
    def test_file_count_limit(self):
        """ファイル数制限を超えるZIPはエラー"""
        files = {"SKILL.md": "# Skill"}
        for i in range(55):
            files[f"file_{i}.txt"] = f"content {i}"

        zip_data = _make_zip(files)
        with pytest.raises(ValidationError):
            validate_zip_archive(zip_data, max_file_count=50)

    @pytest.mark.unit
    def test_total_size_limit(self):
        """展開後サイズ制限を超えるZIPはエラー"""
        zip_data = _make_zip({
            "SKILL.md": "# Skill",
            "large.txt": "x" * (11 * 1024 * 1024),  # 11MB
        })
        with pytest.raises(ValidationError):
            validate_zip_archive(zip_data, max_total_size=10 * 1024 * 1024)

    @pytest.mark.unit
    def test_non_utf8_file_raises_error(self):
        """UTF-8でないファイルを含むZIPはエラー"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", "# Skill")
            zf.writestr("binary.dat", b"\x80\x81\x82\x83".decode("latin-1"))
        # 実際にバイナリをそのまま書き込む
        buf2 = io.BytesIO()
        with zipfile.ZipFile(buf2, "w") as zf:
            zf.writestr("SKILL.md", "# Skill")
            zf.writestr("binary.dat", bytes([0x80, 0x81, 0x82, 0x83]))
        with pytest.raises(ValidationError):
            validate_zip_archive(buf2.getvalue())

    @pytest.mark.unit
    def test_macosx_files_skipped(self):
        """__MACOSX/や.DS_Storeはスキップされる"""
        zip_data = _make_zip({
            "SKILL.md": "# Skill",
            "run.py": "print('hello')",
            "__MACOSX/._run.py": "mac metadata",
            ".DS_Store": "mac store",
        })
        result = validate_zip_archive(zip_data)

        assert len(result) == 2
        assert "SKILL.md" in result
        assert "run.py" in result
        assert "__MACOSX/._run.py" not in result
        assert ".DS_Store" not in result

    @pytest.mark.unit
    def test_directories_in_zip_ignored(self):
        """ZIPディレクトリエントリは無視される"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", "# Skill")
            # ディレクトリエントリを追加
            zf.writestr("lib/", "")
            zf.writestr("lib/helper.py", "# helper")
        result = validate_zip_archive(buf.getvalue())

        assert "SKILL.md" in result
        assert "lib/helper.py" in result
        assert "lib/" not in result

    @pytest.mark.unit
    def test_custom_limits(self):
        """カスタム制限値が適用される"""
        files = {"SKILL.md": "# Skill"}
        for i in range(5):
            files[f"file_{i}.txt"] = f"content {i}"

        zip_data = _make_zip(files)

        # ファイル数制限3: 6ファイルなので失敗
        with pytest.raises(ValidationError):
            validate_zip_archive(zip_data, max_file_count=3)

        # ファイル数制限10: 6ファイルなので成功
        result = validate_zip_archive(zip_data, max_file_count=10)
        assert len(result) == 6
