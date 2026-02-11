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


# MCPコマンドのホワイトリスト（安全と認められるコマンドのみ許可）
# 本番環境では厳格に管理し、必要に応じて追加
MCP_COMMAND_WHITELIST = {
    "npx",
    "node",
    "python",
    "python3",
    "uvx",
    "uv",
}

# シェルインジェクションに使われる危険な文字
SHELL_METACHARACTERS = re.compile(r"[;&|`$(){}\\<>'\"\n\r]")


def validate_mcp_command(command: str, args: list[str] | None = None) -> None:
    """
    MCPサーバーのコマンドを検証する

    Args:
        command: 実行するコマンド
        args: コマンド引数

    Raises:
        ValidationError: コマンドが無効な場合
    """
    if not command:
        raise ValidationError("command", "コマンドが空です")

    # コマンド名を抽出（パス付きの場合はベース名を取得）
    command_name = Path(command).name

    # ホワイトリストチェック
    if command_name not in MCP_COMMAND_WHITELIST:
        raise ValidationError(
            "command",
            f"許可されていないコマンドです: {command_name}。"
            f"許可されたコマンド: {', '.join(sorted(MCP_COMMAND_WHITELIST))}"
        )

    # コマンドにシェルメタ文字が含まれていないかチェック
    if SHELL_METACHARACTERS.search(command):
        raise ValidationError(
            "command",
            "コマンドにシェルメタ文字が含まれています"
        )

    # 引数のチェック
    if args:
        for i, arg in enumerate(args):
            if not isinstance(arg, str):
                raise ValidationError(
                    "args",
                    f"引数は文字列である必要があります: index={i}"
                )
            # 引数にも危険なパターンがないかチェック（シェル展開対策）
            # ただし、引数はシェルを通さずに直接渡されるため、
            # バッククォートとドル記号のみチェック
            if "`" in arg or "$(" in arg:
                raise ValidationError(
                    "args",
                    f"引数にシェル展開パターンが含まれています: index={i}"
                )


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
