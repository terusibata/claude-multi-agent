"""
タイムゾーンユーティリティ

日本時間（JST）をデフォルトとした日時変換機能を提供
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

# 日本標準時（JST: UTC+9）
JST = timezone(timedelta(hours=9))


def to_utc(dt: Optional[datetime], assume_jst: bool = True) -> Optional[datetime]:
    """
    日時をUTCに変換

    Args:
        dt: 変換する日時（Noneの場合はNoneを返す）
        assume_jst: タイムゾーン情報がない場合にJSTとして扱うかどうか

    Returns:
        UTC日時（タイムゾーン付き）、またはNone
    """
    if dt is None:
        return None

    if dt.tzinfo is None:
        # タイムゾーン情報がない場合
        if assume_jst:
            # JSTとして扱い、UTCに変換
            dt = dt.replace(tzinfo=JST)
        else:
            # UTCとして扱う
            dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def to_jst(dt: Optional[datetime]) -> Optional[datetime]:
    """
    日時をJSTに変換

    Args:
        dt: 変換する日時（Noneの場合はNoneを返す）

    Returns:
        JST日時（タイムゾーン付き）、またはNone
    """
    if dt is None:
        return None

    if dt.tzinfo is None:
        # タイムゾーン情報がない場合はUTCとして扱う
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(JST)


def now_utc() -> datetime:
    """
    現在のUTC日時を取得

    Returns:
        現在のUTC日時（タイムゾーン付き）
    """
    return datetime.now(timezone.utc)


def now_jst() -> datetime:
    """
    現在のJST日時を取得

    Returns:
        現在のJST日時（タイムゾーン付き）
    """
    return datetime.now(JST)
