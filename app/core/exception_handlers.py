"""
例外ハンドラー
アプリケーション全体の例外処理を定義
"""
import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.infrastructure.metrics import get_error_counter
from app.schemas.error import ErrorCodes, create_error_response
from app.utils.exceptions import (
    AppError,
    NotFoundError,
    SecurityError,
    ValidationError,
)

logger = structlog.get_logger(__name__)


def _get_request_id(request: Request) -> str | None:
    """リクエストIDを取得"""
    return getattr(request.state, "request_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    """全例外ハンドラーをアプリケーションに登録"""

    @app.exception_handler(NotFoundError)
    async def not_found_error_handler(request: Request, exc: NotFoundError):
        """リソース未検出エラーハンドラー"""
        get_error_counter().inc(type="not_found", code=ErrorCodes.NOT_FOUND)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=create_error_response(
                code=ErrorCodes.NOT_FOUND,
                message=exc.message,
                details=[{"field": exc.resource_type, "message": exc.message}],
                request_id=_get_request_id(request),
            ),
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        """バリデーションエラーハンドラー"""
        get_error_counter().inc(type="validation", code=ErrorCodes.VALIDATION_ERROR)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=create_error_response(
                code=ErrorCodes.VALIDATION_ERROR,
                message=exc.message,
                details=[{"field": exc.field, "message": exc.message}],
                request_id=_get_request_id(request),
            ),
        )

    @app.exception_handler(SecurityError)
    async def security_error_handler(request: Request, exc: SecurityError):
        """セキュリティエラーハンドラー"""
        get_error_counter().inc(type="security", code=exc.error_code)
        logger.warning(
            "セキュリティエラー",
            error_code=exc.error_code,
            details=exc.details,
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=create_error_response(
                code=exc.error_code,
                message=exc.message,
                request_id=_get_request_id(request),
            ),
        )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        """アプリケーションエラーハンドラー"""
        get_error_counter().inc(type="app", code=exc.error_code)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=create_error_response(
                code=exc.error_code,
                message=exc.message,
                request_id=_get_request_id(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ):
        """リクエストバリデーションエラーハンドラー"""
        get_error_counter().inc(
            type="request_validation", code=ErrorCodes.VALIDATION_ERROR
        )
        details = []
        for error in exc.errors():
            loc = error.get("loc", [])
            field = ".".join(str(l) for l in loc) if loc else "unknown"
            details.append(
                {
                    "field": field,
                    "message": error.get("msg", "Invalid value"),
                    "code": error.get("type"),
                }
            )

        logger.warning(
            "バリデーションエラー",
            errors=details,
            path=request.url.path,
        )

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=create_error_response(
                code=ErrorCodes.VALIDATION_ERROR,
                message="入力データが不正です",
                details=details,
                request_id=_get_request_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """一般エラーハンドラー"""
        get_error_counter().inc(type="internal", code=ErrorCodes.INTERNAL_ERROR)
        logger.error(
            "内部エラー",
            error=str(exc),
            error_type=type(exc).__name__,
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=create_error_response(
                code=ErrorCodes.INTERNAL_ERROR,
                message="内部サーバーエラーが発生しました",
                request_id=_get_request_id(request),
            ),
        )
