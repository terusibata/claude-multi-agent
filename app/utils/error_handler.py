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


def raise_inactive_resource(
    resource_type: str,
    resource_id: str,
    expected_status: str = "active",
) -> None:
    """リソースが非アクティブな場合のエラーを発生"""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"{resource_type} '{resource_id}' は無効です（期待される状態: {expected_status}）",
    )


def raise_forbidden(message: str) -> None:
    """403エラーを発生"""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=message,
    )


def raise_validation_error(field: str, message: str) -> None:
    """バリデーションエラーを発生"""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"{field}: {message}",
    )
