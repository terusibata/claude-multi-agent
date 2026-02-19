"""
センシティブ情報フィルター

ログやDB保存前に認証トークン・APIキー等のセンシティブ情報をマスクする。
MCPトークンのプロキシ側注入(Step 1)と併せ、多層防御としてログレベルでも漏洩を防止。
"""

import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# マスク文字列
_MASK = "***REDACTED***"

# ヘッダーキー名のセンシティブパターン（大文字小文字無視）
_SENSITIVE_HEADER_KEYS = re.compile(
    r"(authorization|x-api-key|x-auth-token|proxy-authorization|"
    r"x-secret|api-key|access-token|bearer)",
    re.IGNORECASE,
)

# ヘッダー値内のBearerトークンパターン
_BEARER_PATTERN = re.compile(
    r"(Bearer\s+)\S+",
    re.IGNORECASE,
)

# URLクエリパラメータのセンシティブキーパターン
_SENSITIVE_URL_PARAMS = re.compile(
    r"(token|key|secret|password|api_key|apikey|access_token|auth)",
    re.IGNORECASE,
)

# dict値のセンシティブキーパターン（再帰的にチェック）
_SENSITIVE_DICT_KEYS = re.compile(
    r"(password|secret|token|api_key|apikey|access_key|private_key|"
    r"authorization|credential|auth_token)",
    re.IGNORECASE,
)


def sanitize_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    """認証ヘッダーの値をマスクする

    Args:
        headers: HTTPヘッダー辞書（Noneの場合はそのまま返す）

    Returns:
        マスク済みヘッダー辞書（元のdictは変更しない）
    """
    if not headers:
        return headers

    sanitized = {}
    for key, value in headers.items():
        if _SENSITIVE_HEADER_KEYS.search(key):
            sanitized[key] = _MASK
        elif isinstance(value, str) and _BEARER_PATTERN.search(value):
            sanitized[key] = _BEARER_PATTERN.sub(r"\1" + _MASK, value)
        else:
            sanitized[key] = value
    return sanitized


def sanitize_url(url: str) -> str:
    """URLクエリパラメータ内のセンシティブ情報をマスクする

    Args:
        url: URL文字列

    Returns:
        マスク済みURL文字列
    """
    if not url or "?" not in url:
        return url

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        sanitized_params = {}
        for key, values in params.items():
            if _SENSITIVE_URL_PARAMS.search(key):
                sanitized_params[key] = [_MASK]
            else:
                sanitized_params[key] = values

        sanitized_query = urlencode(sanitized_params, doseq=True)
        return urlunparse(parsed._replace(query=sanitized_query))
    except Exception:
        return url


def sanitize_log_data(data: Any, *, _depth: int = 0) -> Any:
    """再帰的にセンシティブパターンを検出しマスクする

    Args:
        data: サニタイズ対象のデータ（dict, list, str等）

    Returns:
        マスク済みデータ（元のデータは変更しない）
    """
    if _depth > 10:
        return data

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(key, str) and _SENSITIVE_DICT_KEYS.search(key):
                result[key] = _MASK
            else:
                result[key] = sanitize_log_data(value, _depth=_depth + 1)
        return result

    if isinstance(data, list):
        return [sanitize_log_data(item, _depth=_depth + 1) for item in data]

    return data
