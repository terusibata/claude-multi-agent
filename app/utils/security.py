"""
セキュリティユーティリティ
パストラバーサル検証、ファイル名サニタイズなど
"""
import re
from pathlib import Path

from app.utils.exceptions import PathTraversalError, ValidationError


# ファイル名に使用可能な文字パターン
# アルファベット、数字、ハイフン、アンダースコア、ドット、日本語など
SAFE_FILENAME_PATTERN = re.compile(r"^[\w\-.\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+$")

# スキル名に使用可能な文字パターン
# ディレクトリ名として安全な文字のみ
SAFE_SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")

# スラッシュコマンドのパターン（'/'なしで保存、フロントエンドで'/'を付けて表示）
SLASH_COMMAND_PATTERN = re.compile(r"^[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\-]+$")

# パストラバーサル検出パターン
PATH_TRAVERSAL_PATTERNS = [
    "..",
    "~",
    "\x00",  # null byte
]


def validate_path_traversal(path: str, base_path: Path | None = None) -> None:
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

    # 絶対パスの場合はエラー（クロスプラットフォーム対応）
    if Path(path).is_absolute():
        raise PathTraversalError(path)

    # base_path が指定されている場合、正規化後のパスがベース配下か確認
    if base_path is not None:
        try:
            full_path = (base_path / path).resolve()
            base_resolved = base_path.resolve()

            if not full_path.is_relative_to(base_resolved):
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


def validate_slash_command(slash_command: str | None) -> None:
    """
    スラッシュコマンドを検証する

    Note: スラッシュコマンドは'/'なしで保存し、フロントエンドで'/'を付けて表示する

    Args:
        slash_command: スラッシュコマンド（'/'なし）

    Raises:
        ValidationError: スラッシュコマンドが無効な場合
    """
    if slash_command is None:
        return

    if not slash_command:
        raise ValidationError("slash_command", "スラッシュコマンドが空です")

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


def validate_conversation_id(conversation_id: str) -> None:
    """
    会話IDを検証する

    Args:
        conversation_id: 会話ID

    Raises:
        ValidationError: 会話IDが無効な場合
    """
    if not conversation_id:
        raise ValidationError("conversation_id", "会話IDが空です")

    if len(conversation_id) > 200:
        raise ValidationError("conversation_id", "会話IDは200文字以内にしてください")

    # 英数字、ハイフン、アンダースコアのみ許可（UUIDを想定）
    if not re.match(r"^[a-zA-Z0-9_\-]+$", conversation_id):
        raise ValidationError(
            "conversation_id",
            "会話IDには英数字、ハイフン、アンダースコアのみ使用できます"
        )


# ZIP アーカイブ制限
ZIP_MAX_TOTAL_SIZE = 10 * 1024 * 1024  # 展開後合計サイズ上限: 10MB
ZIP_MAX_FILE_COUNT = 50  # 1 Skillあたり最大ファイル数


def validate_zip_archive(
    zip_data: bytes,
    *,
    require_skill_md: bool = True,
    max_total_size: int = ZIP_MAX_TOTAL_SIZE,
    max_file_count: int = ZIP_MAX_FILE_COUNT,
) -> dict[str, bytes]:
    """
    ZIPアーカイブを検証し、ファイルをraw bytesとして展開する

    Args:
        zip_data: ZIPファイルのバイナリデータ
        require_skill_md: SKILL.mdの存在を必須とするか（新規作成時True、更新時False）
        max_total_size: 展開後の合計サイズ上限（バイト）
        max_file_count: 最大ファイル数

    Returns:
        {sanitized_path: raw_bytes} のdict

    Raises:
        ValidationError: ZIPが無効、サイズ超過、ファイル数超過の場合
        PathTraversalError: ZIPエントリにパストラバーサルが含まれる場合
    """
    import io
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_data))
    except zipfile.BadZipFile:
        raise ValidationError("skill_archive", "無効なZIPファイルです")

    with zf:
        entries = [info for info in zf.infolist() if not info.is_dir()]

        # ファイル数チェック
        if len(entries) > max_file_count:
            raise ValidationError(
                "skill_archive",
                f"ファイル数が上限({max_file_count}件)を超えています: {len(entries)}件",
            )

        # 展開後の合計サイズチェック
        total_size = sum(info.file_size for info in entries)
        if total_size > max_total_size:
            size_mb = max_total_size / (1024 * 1024)
            raise ValidationError(
                "skill_archive",
                f"展開後のサイズが上限({size_mb:.0f}MB)を超えています",
            )

        files: dict[str, bytes] = {}
        for info in entries:
            filename = info.filename

            # __MACOSX や .DS_Store などをスキップ
            if filename.startswith("__MACOSX/") or filename.startswith("."):
                continue
            basename = filename.rsplit("/", 1)[-1] if "/" in filename else filename
            if basename.startswith("."):
                continue

            # パストラバーサルチェック + サニタイズ
            safe_name = sanitize_filename(filename)

            # raw bytesとして保持（バイナリファイル対応）
            files[safe_name] = zf.read(info.filename)

        # SKILL.md必須チェック
        if require_skill_md and "SKILL.md" not in files:
            raise ValidationError(
                "skill_archive",
                "ZIPアーカイブにSKILL.mdが含まれていません",
            )

    return files


def validate_file_path(file_path: str, base_path: Path) -> Path:
    """
    ファイルパスを検証し、安全なパスを返す

    Args:
        file_path: 検証するファイルパス
        base_path: ベースディレクトリ

    Returns:
        検証済みの絶対パス

    Raises:
        PathTraversalError: パストラバーサルを検出した場合
        ValidationError: ファイルパスが無効な場合
    """
    if not file_path:
        raise ValidationError("file_path", "ファイルパスが空です")

    # パストラバーサルチェック
    validate_path_traversal(file_path, base_path)

    # 安全なパスを返す
    return (base_path / file_path).resolve()
