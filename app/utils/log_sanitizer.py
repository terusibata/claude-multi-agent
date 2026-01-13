"""
ログサニタイザー

センシティブ情報をマスクしてログに出力するためのユーティリティ
"""
import re
from typing import Any
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


# マスクするキー名のパターン（大文字小文字を区別しない）
SENSITIVE_KEY_PATTERNS = [
    re.compile(r".*password.*", re.IGNORECASE),
    re.compile(r".*secret.*", re.IGNORECASE),
    re.compile(r".*token.*", re.IGNORECASE),
    re.compile(r".*api[_-]?key.*", re.IGNORECASE),
    re.compile(r".*access[_-]?key.*", re.IGNORECASE),
    re.compile(r".*auth.*", re.IGNORECASE),
    re.compile(r".*credential.*", re.IGNORECASE),
    re.compile(r".*private.*", re.IGNORECASE),
    re.compile(r".*bearer.*", re.IGNORECASE),
]

# URLのクエリパラメータでマスクするキー
SENSITIVE_URL_PARAMS = {
    "token",
    "api_key",
    "apikey",
    "access_token",
    "auth",
    "key",
    "secret",
    "password",
}

# マスク文字列
MASK = "***"


def is_sensitive_key(key: str) -> bool:
    """
    キー名がセンシティブかどうかを判定

    Args:
        key: キー名

    Returns:
        センシティブならTrue
    """
    return any(pattern.match(key) for pattern in SENSITIVE_KEY_PATTERNS)


def mask_value(value: Any, key: str = "") -> Any:
    """
    値をマスクする

    Args:
        value: マスクする値
        key: キー名（オプション、センシティブ判定に使用）

    Returns:
        マスクされた値
    """
    if value is None:
        return None

    if isinstance(value, str):
        # 空文字列はそのまま
        if not value:
            return value
        # キーがセンシティブなら完全マスク
        if key and is_sensitive_key(key):
            return MASK
        # URLの場合はセンシティブなパラメータをマスク
        if value.startswith(("http://", "https://")):
            return mask_url(value)
        return value

    if isinstance(value, dict):
        return mask_dict(value)

    if isinstance(value, list):
        return [mask_value(item) for item in value]

    return value


def mask_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    辞書内のセンシティブな値をマスク

    Args:
        data: マスクする辞書

    Returns:
        マスクされた辞書
    """
    masked = {}
    for key, value in data.items():
        if is_sensitive_key(key):
            # センシティブなキーは値を完全マスク
            if isinstance(value, str) and value:
                masked[key] = MASK
            elif isinstance(value, dict):
                masked[key] = MASK
            elif isinstance(value, list) and value:
                masked[key] = [MASK]
            else:
                masked[key] = MASK
        else:
            # 再帰的に処理
            masked[key] = mask_value(value, key)

    return masked


def mask_url(url: str) -> str:
    """
    URL内のセンシティブなクエリパラメータをマスク

    Args:
        url: マスクするURL

    Returns:
        マスクされたURL
    """
    try:
        parsed = urlparse(url)
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            masked_params = {}
            for key, values in params.items():
                if key.lower() in SENSITIVE_URL_PARAMS:
                    masked_params[key] = [MASK]
                else:
                    masked_params[key] = values
            masked_query = urlencode(masked_params, doseq=True)
            return urlunparse(parsed._replace(query=masked_query))
        return url
    except Exception:
        # パース失敗時はそのまま返す
        return url


def sanitize_sdk_options(options: dict[str, Any]) -> dict[str, Any]:
    """
    SDK オプションをサニタイズ

    Args:
        options: SDKオプション

    Returns:
        サニタイズされたオプション
    """
    # 深いコピーを避けるため、必要な部分のみマスク
    sanitized = {}

    for key, value in options.items():
        if key in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token"):
            sanitized[key] = MASK if value else None
        elif key == "env":
            # 環境変数はセンシティブな可能性が高い
            sanitized[key] = mask_dict(value) if isinstance(value, dict) else value
        elif key == "system_prompt":
            # システムプロンプトは長すぎるため省略
            if isinstance(value, str) and len(value) > 100:
                sanitized[key] = value[:100] + "... (truncated)"
            else:
                sanitized[key] = value
        elif key == "mcp_servers":
            # MCPサーバー設定をサニタイズ
            if isinstance(value, dict):
                sanitized[key] = {
                    name: mask_dict(config) if isinstance(config, dict) else config
                    for name, config in value.items()
                }
            else:
                sanitized[key] = value
        else:
            sanitized[key] = mask_value(value, key)

    return sanitized


def sanitize_for_log(data: Any) -> Any:
    """
    ログ出力用にデータをサニタイズ

    Args:
        data: サニタイズするデータ

    Returns:
        サニタイズされたデータ
    """
    if isinstance(data, dict):
        return mask_dict(data)
    elif isinstance(data, list):
        return [sanitize_for_log(item) for item in data]
    elif isinstance(data, str):
        return mask_value(data)
    return data
