# -*- coding: utf-8 -*-
"""
ファイルツール共通ユーティリティ

各Office形式ツール（Word/Excel/PowerPoint）で共有される
ユーティリティ関数群。テキスト正規化、レスポンス生成、
旧形式チェック等の重複ロジックを一元管理する。
"""

import re
import unicodedata
from functools import wraps
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def normalize_text(text: str) -> str:
    """
    テキストの正規化

    - Unicode NFC正規化
    - 制御文字を除去（改行・タブは保持）
    - ゼロ幅文字・BOMを除去
    """
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)

    result = []
    for ch in text:
        code = ord(ch)
        if ch in ('\n', '\r', '\t'):
            result.append(ch)
        elif code < 0x20 or code == 0x7F or (0x80 <= code <= 0x9F):
            continue
        elif code in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
            continue
        else:
            result.append(ch)

    return "".join(result)


def create_context_snippet(
    text: str, match_start: int, match_end: int, context_chars: int = 40
) -> str:
    """
    検索マッチの前後コンテキストを生成

    マッチ部分を [brackets] で囲み、前後に context_chars 文字のコンテキストを付与。
    テキストの先頭/末尾でない場合は "..." を付加。
    """
    start = max(0, match_start - context_chars)
    end = min(len(text), match_end + context_chars)

    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""

    before = text[start:match_start]
    match = text[match_start:match_end]
    after = text[match_end:end]

    return f"{prefix}{before}[{match}]{after}{suffix}"


def check_old_format(
    file_path: str,
    old_extension: str,
    format_name: str,
    new_extension: str,
    library_name: str,
    application_name: str,
) -> dict[str, Any] | None:
    """
    古いOffice形式のファイルかどうかをチェック

    Args:
        file_path: チェック対象のファイルパス
        old_extension: 旧形式の拡張子（例: ".doc"）
        format_name: 形式名（例: "Word"）
        new_extension: 新形式の拡張子（例: ".docx"）
        library_name: 使用ライブラリ名（例: "python-docx"）
        application_name: アプリケーション名（例: "Microsoft Word"）

    Returns:
        旧形式の場合はエラーレスポンスdict、それ以外はNone
    """
    if file_path.lower().endswith(old_extension):
        return format_tool_error(
            f"エラー: '{file_path}' は古い{format_name}形式（{old_extension}）です。\n\n"
            f"このツールは {new_extension}（Office Open XML）形式のみ対応しています。\n"
            f"{old_extension}（バイナリ形式）ファイルは {library_name} では読み取れません。\n\n"
            f"対処方法:\n"
            f"1. {application_name} で {new_extension} 形式に変換して再アップロード\n"
            f"2. LibreOffice で {new_extension} 形式に変換して再アップロード\n"
            f"3. オンライン変換ツールを使用"
        )
    return None


def build_search_pattern(query: str, case_sensitive: bool = False) -> re.Pattern:
    """
    検索用正規表現パターンを構築

    Args:
        query: 検索クエリ（自動的にエスケープされる）
        case_sensitive: 大文字小文字を区別するか

    Returns:
        コンパイル済みの正規表現パターン

    Raises:
        ValueError: 無効な検索クエリの場合
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(re.escape(query), flags)
    except re.error:
        raise ValueError(f"無効な検索クエリ: {query}")


def format_tool_error(message: str) -> dict[str, Any]:
    """ツールエラーレスポンスを生成"""
    return {
        "content": [{"type": "text", "text": message}],
        "is_error": True,
    }


def format_tool_success(text: str) -> dict[str, Any]:
    """ツール成功レスポンスを生成"""
    return {"content": [{"type": "text", "text": text}]}


def check_library_available(
    module_name: str, display_name: str
) -> dict[str, Any] | None:
    """
    ライブラリの利用可能性を確認

    Args:
        module_name: importするモジュール名（例: "docx"）
        display_name: エラー表示用の名前（例: "python-docx"）

    Returns:
        利用不可の場合はエラーレスポンスdict、利用可能ならNone
    """
    try:
        __import__(module_name)
        return None
    except ImportError:
        return format_tool_error(
            f"エラー: {display_name}ライブラリがインストールされていません。"
        )


# =============================================================================
# ハンドラーデコレータ
# =============================================================================


def file_tool_handler(
    *,
    old_format: tuple[str, str, str, str, str] | None = None,
    required_library: tuple[str, str] | None = None,
    log_prefix: str = "ファイルツール",
):
    """
    ファイルツールハンドラーの共通処理をデコレータ化。

    ファイルダウンロード、旧形式チェック、ライブラリ確認、
    エラーハンドリングを統一的に処理する。

    デコレート対象の関数シグネチャ:
        async def handler(*, content, filename, content_type, args, **ctx)

    ctx にはワークスペース操作用のパラメータが含まれる:
        workspace_service, tenant_id, conversation_id

    Args:
        old_format: 旧形式チェック用タプル
            (旧拡張子, 形式名, 新拡張子, ライブラリ名, アプリ名)
        required_library: ライブラリ確認用タプル (モジュール名, 表示名)
        log_prefix: エラーログのプレフィックス
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(workspace_service, tenant_id, conversation_id, args):
            file_path = args.get("file_path", "")

            # 旧形式チェック
            if old_format:
                err = check_old_format(file_path, *old_format)
                if err:
                    return err

            # ライブラリ確認
            if required_library:
                err = check_library_available(*required_library)
                if err:
                    return err

            try:
                content, filename, content_type = (
                    await workspace_service.download_file(
                        tenant_id, conversation_id, file_path
                    )
                )
                return await func(
                    content=content,
                    filename=filename,
                    content_type=content_type,
                    args=args,
                    workspace_service=workspace_service,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                )
            except FileNotFoundError:
                return format_tool_error(
                    f"ファイルが見つかりません: {file_path}"
                )
            except ValueError as e:
                return format_tool_error(str(e))
            except Exception as e:
                logger.error(
                    f"{log_prefix}エラー",
                    error=str(e),
                    file_path=file_path,
                )
                return format_tool_error(f"読み込みエラー: {str(e)}")

        return wrapper
    return decorator
