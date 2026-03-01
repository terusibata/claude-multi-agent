"""
ZIP Skills アップロード/ダウンロードのユニットテスト

ZIPアーカイブの検証・展開ロジックをテストする。
DB/コンテナ不要の純粋ユニットテスト。
"""
import io
import zipfile

import pytest

from app.utils.exceptions import ValidationError
from app.utils.security import validate_zip_archive


def _make_zip(files: dict[str, str | bytes]) -> bytes:
    """テスト用ZIPアーカイブを作成

    values が str の場合はテキスト、bytes の場合はバイナリとして書き込む。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestValidateZipArchive:
    """validate_zip_archive のテスト"""

    @pytest.mark.unit
    def test_valid_zip_with_skill_md(self):
        """SKILL.mdを含む有効なZIPが正常に展開される（bytes型で返却）"""
        zip_data = _make_zip({
            "SKILL.md": "# My Skill\nDescription",
            "run.py": "print('hello')",
        })
        result = validate_zip_archive(zip_data)

        assert "SKILL.md" in result
        assert "run.py" in result
        # 結果は bytes 型
        assert isinstance(result["SKILL.md"], bytes)
        assert result["SKILL.md"] == b"# My Skill\nDescription"
        assert result["run.py"] == b"print('hello')"

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
        files: dict[str, str | bytes] = {"SKILL.md": "# Skill"}
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
    def test_binary_file_accepted(self):
        """バイナリファイルを含むZIPは正常に展開される"""
        binary_content = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])  # PNG header
        zip_data = _make_zip({
            "SKILL.md": "# Skill",
            "image.png": binary_content,
        })
        result = validate_zip_archive(zip_data)

        assert "SKILL.md" in result
        assert "image.png" in result
        assert result["image.png"] == binary_content
        assert isinstance(result["image.png"], bytes)

    @pytest.mark.unit
    def test_mixed_text_and_binary(self):
        """テキストとバイナリが混在するZIPが正常に展開される"""
        png_bytes = bytes(range(256))  # 全バイト値を含むバイナリ
        zip_data = _make_zip({
            "SKILL.md": "# My Skill",
            "run.py": "print('hello')",
            "data/image.png": png_bytes,
            "config.json": '{"key": "value"}',
        })
        result = validate_zip_archive(zip_data)

        assert len(result) == 4
        assert result["SKILL.md"] == b"# My Skill"
        assert result["run.py"] == b"print('hello')"
        assert result["data/image.png"] == png_bytes
        assert result["config.json"] == b'{"key": "value"}'

    @pytest.mark.unit
    def test_binary_file_preserved_in_zip(self):
        """バイナリファイルがbytesとして正確に保持される"""
        # 非UTF-8バイト列
        raw_bytes = bytes([0x80, 0x81, 0x82, 0x83, 0xFF, 0xFE, 0x00, 0x01])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", "# Skill")
            zf.writestr("binary.dat", raw_bytes)
        result = validate_zip_archive(buf.getvalue())

        assert "binary.dat" in result
        assert result["binary.dat"] == raw_bytes

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
        files: dict[str, str | bytes] = {"SKILL.md": "# Skill"}
        for i in range(5):
            files[f"file_{i}.txt"] = f"content {i}"

        zip_data = _make_zip(files)

        # ファイル数制限3: 6ファイルなので失敗
        with pytest.raises(ValidationError):
            validate_zip_archive(zip_data, max_file_count=3)

        # ファイル数制限10: 6ファイルなので成功
        result = validate_zip_archive(zip_data, max_file_count=10)
        assert len(result) == 6

    @pytest.mark.unit
    def test_require_skill_md_false(self):
        """require_skill_md=FalseでSKILL.mdなしでも成功する"""
        zip_data = _make_zip({
            "run.py": "print('hello')",
        })
        result = validate_zip_archive(zip_data, require_skill_md=False)

        assert "run.py" in result
        assert result["run.py"] == b"print('hello')"
