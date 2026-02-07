"""
AIエージェントバックエンド メインアプリケーション
コンテナ隔離型マルチテナント対応AIエージェントシステム
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import aiodocker
import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from redis.asyncio import Redis

from app import __version__
from app.api import api_router
from app.api.health import router as health_router
from app.config import get_settings
from app.database import close_db, get_pool_status
from app.infrastructure.metrics import (
    get_active_connections,
    get_db_pool_gauge,
    get_error_counter,
    get_metrics_registry,
    get_request_counter,
    get_request_duration,
    measure_time,
)
from app.infrastructure.redis import close_redis_pool, get_pool_info, get_redis_pool
from app.infrastructure.shutdown import get_shutdown_manager
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.tracing import TracingMiddleware
from app.schemas.error import ErrorCodes, create_error_response
from app.services.container.gc import ContainerGarbageCollector
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.container.warm_pool import WarmPoolManager
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
    level=settings.log_level_int,
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

    コンテナ隔離アーキテクチャ:
      - aiodocker → ContainerLifecycleManager
      - WarmPoolManager（プレウォーム済みコンテナプール）
      - ContainerOrchestrator（会話→コンテナマッピング）
      - ContainerGarbageCollector（TTL超過コンテナ回収）
    """
    # シャットダウンマネージャーを取得
    shutdown_manager = get_shutdown_manager()

    # 起動時
    logger.info(
        "アプリケーション起動中...",
        version=__version__,
        environment=settings.app_env,
    )

    # シグナルハンドラーを設定
    try:
        loop = asyncio.get_running_loop()
        shutdown_manager.setup_signal_handlers(loop)
    except Exception as e:
        logger.warning("シグナルハンドラー設定エラー", error=str(e))

    # ---- コンテナ隔離スタック初期化 ----
    docker_client = aiodocker.Docker(url=settings.docker_socket_path)
    logger.info("Dockerクライアント初期化完了", socket=settings.docker_socket_path)

    # Redis接続プール取得
    redis_pool = await get_redis_pool()
    redis = Redis(connection_pool=redis_pool)

    # コンテナライフサイクルマネージャー
    lifecycle = ContainerLifecycleManager(docker_client)

    # WarmPoolマネージャー
    warm_pool = WarmPoolManager(lifecycle, redis)

    # コンテナオーケストレーター
    orchestrator = ContainerOrchestrator(lifecycle, warm_pool, redis)

    # アプリケーション状態に保存（APIエンドポイントから参照）
    app.state.orchestrator = orchestrator
    app.state.docker_client = docker_client

    # GC（ガベージコレクター）
    gc = ContainerGarbageCollector(lifecycle, redis)
    app.state.gc = gc

    # WarmPoolの初期補充
    try:
        await warm_pool.replenish()
        pool_size = await warm_pool.get_pool_size()
        logger.info("WarmPool初期化完了", pool_size=pool_size)
    except Exception as e:
        logger.error("WarmPool初期化エラー", error=str(e))

    # GCループ開始
    try:
        await gc.start(interval=settings.container_gc_interval)
        logger.info("コンテナGC開始", interval=settings.container_gc_interval)
    except Exception as e:
        logger.error("GC開始エラー", error=str(e))

    logger.info(
        "コンテナ隔離スタック初期化完了",
        warm_pool_min=settings.warm_pool_min_size,
        warm_pool_max=settings.warm_pool_max_size,
        container_image=settings.container_image,
    )

    # セキュリティ設定のログ出力
    if settings.api_keys_list:
        logger.info("API認証が有効化されています", key_count=len(settings.api_keys_list))
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

    if settings.metrics_enabled:
        logger.info("メトリクス収集が有効化されています")

    logger.info(
        "アプリケーション起動完了",
        environment=settings.app_env,
        port=settings.app_port,
    )

    yield

    # ---- 終了時 ----
    logger.info("アプリケーション終了中...")

    # グレースフルシャットダウンを実行
    await shutdown_manager.graceful_shutdown()

    # GC停止
    try:
        await gc.stop()
        logger.info("コンテナGC停止完了")
    except Exception as e:
        logger.error("GC停止エラー", error=str(e))

    # 全コンテナ破棄
    try:
        await orchestrator.destroy_all()
        logger.info("全コンテナ破棄完了")
    except Exception as e:
        logger.error("コンテナ破棄エラー", error=str(e))

    # Dockerクライアントクローズ
    try:
        await docker_client.close()
        logger.info("Dockerクライアントクローズ完了")
    except Exception as e:
        logger.error("Dockerクライアントクローズエラー", error=str(e))

    # Redisクライアントクローズ
    try:
        await redis.aclose()
    except Exception:
        pass

    # リソースをクリーンアップ
    try:
        await close_db()
    except Exception as e:
        logger.error("DBクローズエラー", error=str(e))

    try:
        await close_redis_pool()
    except Exception as e:
        logger.error("Redisクローズエラー", error=str(e))

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
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    """リクエストバリデーションエラーハンドラー"""
    get_error_counter().inc(type="request_validation", code=ErrorCodes.VALIDATION_ERROR)
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


# メトリクスエンドポイント
@app.get("/metrics", tags=["監視"], include_in_schema=settings.is_development)
async def metrics():
    """
    Prometheusメトリクスエンドポイント

    アプリケーションメトリクスをPrometheus形式で返します。
    """
    if not settings.metrics_enabled:
        return PlainTextResponse("Metrics disabled", status_code=404)

    # DBプール状態を更新
    try:
        pool_status = get_pool_status()
        db_gauge = get_db_pool_gauge()
        db_gauge.set(pool_status.get("checked_in", 0), state="idle")
        db_gauge.set(pool_status.get("checked_out", 0), state="active")
        db_gauge.set(pool_status.get("overflow", 0), state="overflow")
    except Exception:
        pass

    # Redisプール状態を更新
    try:
        redis_info = get_pool_info()
        if redis_info.get("initialized"):
            connections_gauge = get_active_connections()
            connections_gauge.set(redis_info.get("max_connections", 0), type="redis_max")
    except Exception:
        pass

    registry = get_metrics_registry()
    return PlainTextResponse(
        registry.export_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


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
        workers=settings.uvicorn_workers if not settings.is_development else 1,
        timeout_keep_alive=settings.uvicorn_timeout_keep_alive,
        timeout_notify=settings.uvicorn_timeout_notify,
    )
