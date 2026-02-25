"""
エラーハンドリングユーティリティ
API層での共通エラー処理
"""
from fastapi import HTTPException, status


def raise_not_found(
    resource_type: str,
    resource_id: str,
    message: str | None = None,
) -> None:
    """404エラーを発生"""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=message or f"{resource_type} '{resource_id}' が見つかりません",
    )
