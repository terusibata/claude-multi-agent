"""
エラーハンドリングユーティリティ
API層での共通エラー処理
"""
import structlog
from fastapi import HTTPException, status
from functools import wraps
from typing import Callable, TypeVar

from app.utils.exceptions import (
    AppError,
    NotFoundError,
    ValidationError,
    InactiveResourceError,
    SecurityError,
    WorkspaceSecurityError,
    FileSizeError,
    SDKError,
)

logger = structlog.get_logger(__name__)

T = TypeVar("T")


def exception_to_http_status(exception: Exception) -> int:
    """例外をHTTPステータスコードに変換"""
    status_mapping = {
        NotFoundError: status.HTTP_404_NOT_FOUND,
        ValidationError: status.HTTP_400_BAD_REQUEST,
        InactiveResourceError: status.HTTP_400_BAD_REQUEST,
        SecurityError: status.HTTP_403_FORBIDDEN,
        WorkspaceSecurityError: status.HTTP_403_FORBIDDEN,
        FileSizeError: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        SDKError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        AppError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }

    for exc_type, http_status in status_mapping.items():
        if isinstance(exception, exc_type):
            return http_status

    return status.HTTP_500_INTERNAL_SERVER_ERROR


def app_error_to_http_exception(error: AppError) -> HTTPException:
    """AppErrorをHTTPExceptionに変換"""
    return HTTPException(
        status_code=exception_to_http_status(error),
        detail={
            "message": error.message,
            "error_code": error.error_code,
            "details": error.details,
        },
    )


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


def handle_service_errors(
    operation_name: str,
    error_message: str = "操作に失敗しました",
):
    """
    サービス層のエラーをハンドリングするデコレータ

    Args:
        operation_name: 操作名（ログ用）
        error_message: デフォルトエラーメッセージ
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                # HTTPExceptionはそのまま再送出
                raise
            except AppError as e:
                logger.error(
                    f"{operation_name}エラー",
                    error_code=e.error_code,
                    error_message=e.message,
                    details=e.details,
                )
                raise app_error_to_http_exception(e)
            except Exception as e:
                logger.error(
                    f"{operation_name}エラー",
                    error=str(e),
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=error_message,
                )

        return wrapper
    return decorator
