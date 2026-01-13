"""
セキュリティユーティリティ
パストラバーサル検証、ファイル名サニタイズなど
"""
import re
from pathlib import Path
from typing import Optional

from app.utils.exceptions import PathTraversalError, ValidationError


# ファイル名に使用可能な文字パターン
# アルファベット、数字、ハイフン、アンダースコア、ドット、日本語など
SAFE_FILENAME_PATTERN = re.compile(r"^[\w\-.\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+$")

# スキル名に使用可能な文字パターン
# ディレクトリ名として安全な文字のみ
SAFE_SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")

# スラッシュコマンドのパターン
SLASH_COMMAND_PATTERN = re.compile(r"^/[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\-]+$")

# パストラバーサル検出パターン
PATH_TRAVERSAL_PATTERNS = [
    "..",
    "~",
    "\x00",  # null byte
]


def validate_path_traversal(path: str, base_path: Optional[Path] = None) -> None:
    """
    パストラバーサル攻撃をチェックする

    Args:
        path: チェックするパス
        base_path: ベースパス（指定時は resolved path がベース配下かもチェック）

    Raises:
        PathTraversalError: パストラバーサルを検出した場合
    """
    # 明らかな攻撃パターンをチェック
    for pattern in PATH_TRAVERSAL_PATTERNS:
        if pattern in path:
            raise PathTraversalError(path)

    # 絶対パスの場合はエラー
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise PathTraversalError(path)

    # base_path が指定されている場合、正規化後のパスがベース配下か確認
    if base_path is not None:
        try:
            full_path = (base_path / path).resolve()
            base_resolved = base_path.resolve()

            # パスがベースディレクトリ配下にあることを確認
            if not str(full_path).startswith(str(base_resolved) + "/") and full_path != base_resolved:
                raise PathTraversalError(path)
        except (OSError, ValueError):
            raise PathTraversalError(path)


def sanitize_filename(filename: str) -> str:
    """
    ファイル名をサニタイズする

    Args:
        filename: 元のファイル名

    Returns:
        サニタイズされたファイル名

    Raises:
        ValidationError: ファイル名が無効な場合
    """
    if not filename:
        raise ValidationError("filename", "ファイル名が空です")

    # パス区切り文字を取り除いてファイル名部分のみ取得
    # サブディレクトリを含む場合は維持する
    parts = filename.replace("\\", "/").split("/")
    sanitized_parts = []

    for part in parts:
        if not part:
            continue

        # パストラバーサルパターンをチェック
        if part in (".", ".."):
            raise ValidationError("filename", f"無効なパスコンポーネント: {part}")

        # ファイル名として安全な文字のみか確認
        if not SAFE_FILENAME_PATTERN.match(part):
            # 安全でない文字を除去
            safe_part = re.sub(r"[^\w\-.\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", "_", part)
            if not safe_part or safe_part == ".":
                raise ValidationError("filename", f"無効なファイル名: {part}")
            part = safe_part

        sanitized_parts.append(part)

    if not sanitized_parts:
        raise ValidationError("filename", "ファイル名が空です")

    return "/".join(sanitized_parts)


def validate_skill_name(name: str) -> None:
    """
    スキル名を検証する

    Args:
        name: スキル名

    Raises:
        ValidationError: スキル名が無効な場合
    """
    if not name:
        raise ValidationError("name", "スキル名が空です")

    if len(name) > 200:
        raise ValidationError("name", "スキル名は200文字以内にしてください")

    if not SAFE_SKILL_NAME_PATTERN.match(name):
        raise ValidationError(
            "name",
            "スキル名には英数字、ハイフン、アンダースコアのみ使用できます"
        )

    # パストラバーサルチェック
    validate_path_traversal(name)


def validate_slash_command(slash_command: Optional[str]) -> None:
    """
    スラッシュコマンドを検証する

    Args:
        slash_command: スラッシュコマンド

    Raises:
        ValidationError: スラッシュコマンドが無効な場合
    """
    if slash_command is None:
        return

    if not slash_command:
        raise ValidationError("slash_command", "スラッシュコマンドが空です")

    if not slash_command.startswith("/"):
        raise ValidationError("slash_command", "スラッシュコマンドは '/' で始める必要があります")

    if len(slash_command) > 100:
        raise ValidationError("slash_command", "スラッシュコマンドは100文字以内にしてください")

    if not SLASH_COMMAND_PATTERN.match(slash_command):
        raise ValidationError(
            "slash_command",
            "スラッシュコマンドには英数字、ハイフン、日本語のみ使用できます"
        )


def validate_tenant_id(tenant_id: str) -> None:
    """
    テナントIDを検証する

    Args:
        tenant_id: テナントID

    Raises:
        ValidationError: テナントIDが無効な場合
    """
    if not tenant_id:
        raise ValidationError("tenant_id", "テナントIDが空です")

    if len(tenant_id) > 100:
        raise ValidationError("tenant_id", "テナントIDは100文字以内にしてください")

    # 英数字、ハイフン、アンダースコアのみ許可
    if not re.match(r"^[a-zA-Z0-9_\-]+$", tenant_id):
        raise ValidationError(
            "tenant_id",
            "テナントIDには英数字、ハイフン、アンダースコアのみ使用できます"
        )

    # パストラバーサルチェック
    validate_path_traversal(tenant_id)
