"""
アプリケーションファクトリ
FastAPIアプリケーションの作成と設定
"""
import logging
import sys

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app import __version__
from app.api import api_router
from app.api.health import router as health_router
from app.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.lifespan import lifespan
from app.core.metrics_endpoint import metrics_handler
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.tracing import TracingMiddleware


def _configure_logging(settings) -> None:
    """ログ設定の初期化"""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level_int,
    )

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


def _register_middleware(app: FastAPI, settings) -> None:
    """
    ミドルウェアを登録

    適用順序は逆順になる点に注意:
      5. TracingMiddleware（最も外側、リクエスト時に最初に適用）
      4. AuthMiddleware
      3. RateLimitMiddleware
      2. CORSMiddleware
      1. SecurityHeadersMiddleware（最も内側、レスポンス時に最後に適用）
    """
    # 1. セキュリティヘッダー（最も内側）
    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.hsts_enabled,
        hsts_max_age=settings.hsts_max_age,
    )

    # 2. CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=settings.cors_methods_list,
        allow_headers=settings.cors_headers_list,
        expose_headers=[
            "X-Request-ID",
            "X-Process-Time",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )

    # 3. レート制限
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_window=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_period,
    )

    # 4. API認証
    app.add_middleware(
        AuthMiddleware,
        api_keys=settings.api_keys_list,
    )

    # 5. リクエストトレーシング（最も外側）
    app.add_middleware(
        TracingMiddleware,
        log_requests=True,
    )


def _register_routes(app: FastAPI, settings) -> None:
    """ルーターとエンドポイントを登録"""

    @app.get("/", tags=["ルート"])
    async def root():
        """ルートエンドポイント - APIの基本情報を返す"""
        return {
            "name": "AIエージェントバックエンド",
            "version": __version__,
            "docs_url": "/docs" if settings.is_development else None,
        }

    @app.get("/metrics", tags=["監視"], include_in_schema=settings.is_development)
    async def metrics():
        """Prometheusメトリクスエンドポイント"""
        if not settings.metrics_enabled:
            return PlainTextResponse("Metrics disabled", status_code=404)
        return await metrics_handler(app.state)

    # ヘルスチェックルーター（ルートレベル）
    app.include_router(health_router)

    # APIルーター
    app.include_router(api_router, prefix="/api")


def create_app() -> FastAPI:
    """
    FastAPIアプリケーションを作成・設定

    Returns:
        設定済みのFastAPIアプリケーション
    """
    settings = get_settings()

    # ログ設定
    _configure_logging(settings)

    # FastAPIインスタンス作成
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

    # ミドルウェア登録
    _register_middleware(app, settings)

    # 例外ハンドラー登録
    register_exception_handlers(app)

    # ルーター・エンドポイント登録
    _register_routes(app, settings)

    return app
