"""
AIエージェントバックエンド メインアプリケーション
AWS Bedrock + Claude Agent SDKを利用したマルチテナント対応AIエージェントシステム
"""
import logging
import os
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import api_router
from app.api.health import router as health_router
from app.config import get_settings
from app.database import close_db
from app.infrastructure.redis import close_redis_pool
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.tracing import TracingMiddleware
from app.schemas.error import ErrorCodes, create_error_response
from app.utils.exceptions import (
    AppError,
    NotFoundError,
    SecurityError,
    ValidationError,
)

# 設定読み込み
settings = get_settings()

# 標準ライブラリのlogging設定
logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=logging.INFO,
)

# ログ設定（structlog contextvars対応）
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    アプリケーションのライフサイクル管理
    起動時・終了時の処理を定義
    """
    # 起動時
    logger.info("アプリケーション起動中...", version=__version__)

    # AWS Bedrock環境変数の設定
    os.environ["CLAUDE_CODE_USE_BEDROCK"] = settings.claude_code_use_bedrock
    if settings.aws_region:
        os.environ["AWS_REGION"] = settings.aws_region
    if settings.aws_access_key_id:
        os.environ["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        os.environ["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key

    # データベース初期化は Alembic マイグレーションで実施
    logger.info("データベースマイグレーションは alembic upgrade head で実行してください")

    # セキュリティ設定のログ出力
    if settings.api_keys_list:
        logger.info("API認証が有効化されています")
    else:
        logger.warning(
            "API認証が無効化されています",
            reason="API_KEYSが設定されていません",
        )

    if settings.rate_limit_enabled:
        logger.info(
            "レート制限が有効化されています",
            requests=settings.rate_limit_requests,
            period=settings.rate_limit_period,
        )

    logger.info("アプリケーション起動完了", environment=settings.app_env)

    yield

    # 終了時
    logger.info("アプリケーション終了中...")
    await close_db()
    await close_redis_pool()
    logger.info("アプリケーション終了完了")


# FastAPIアプリケーション作成
app = FastAPI(
    title="AIエージェントバックエンド",
    description="""
## 概要

AWS Bedrock + Claude Agent SDKを利用したマルチテナント対応AIエージェントシステムのバックエンドAPIです。

## 主要機能

- **モデル管理**: 利用可能なAIモデルの定義と料金管理
- **エージェント設定**: テナントごとのエージェント実行設定
- **Agent Skills管理**: ファイルシステムベースのSkills管理
- **MCPサーバー管理**: Model Context Protocolサーバーの設定
- **エージェント実行**: ストリーミング対応のエージェント実行
- **セッション管理**: 会話履歴とセッションの管理
- **使用状況監視**: トークン使用量とコストのレポート

## 認証

APIへのアクセスにはAPIキーが必要です。
`X-API-Key` ヘッダーまたは `Authorization: Bearer <key>` ヘッダーでAPIキーを送信してください。
    """,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)

# ミドルウェア設定（適用順序は逆順になる点に注意）

# 1. セキュリティヘッダー（最も内側、レスポンス時に最後に適用）
app.add_middleware(
    SecurityHeadersMiddleware,
    enable_hsts=settings.hsts_enabled,
    hsts_max_age=settings.hsts_max_age,
)

# 2. CORS（セキュリティヘッダーの外側）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=settings.cors_methods_list,
    allow_headers=settings.cors_headers_list,
    expose_headers=["X-Request-ID", "X-Process-Time", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)

# 3. レート制限（CORSの外側）
app.add_middleware(
    RateLimitMiddleware,
    requests_per_window=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_period,
)

# 4. API認証（レート制限の外側）
app.add_middleware(
    AuthMiddleware,
    api_keys=settings.api_keys_list,
)

# 5. リクエストトレーシング（最も外側、リクエスト時に最初に適用）
app.add_middleware(
    TracingMiddleware,
    log_requests=True,
)


def _get_request_id(request: Request) -> str | None:
    """リクエストIDを取得"""
    return getattr(request.state, "request_id", None)


# エラーハンドラー

@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    """リソース未検出エラーハンドラー"""
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
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=create_error_response(
            code=exc.error_code,
            message=exc.message,
            request_id=_get_request_id(request),
        ),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    """リクエストバリデーションエラーハンドラー"""
    details = []
    for error in exc.errors():
        loc = error.get("loc", [])
        field = ".".join(str(l) for l in loc) if loc else "unknown"
        details.append({
            "field": field,
            "message": error.get("msg", "Invalid value"),
            "code": error.get("type"),
        })

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


# ルートエンドポイント
@app.get("/", tags=["ルート"])
async def root():
    """
    ルートエンドポイント

    APIの基本情報を返します。
    """
    return {
        "name": "AIエージェントバックエンド",
        "version": __version__,
        "docs_url": "/docs" if settings.is_development else None,
    }


# ヘルスチェックルーターを登録（ルートレベル）
app.include_router(health_router)

# APIルーターを登録
app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=settings.is_development,
    )
