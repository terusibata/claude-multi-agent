"""
アプリケーションライフサイクル管理
起動時・終了時の処理を定義
"""
import asyncio
from contextlib import asynccontextmanager

import aiodocker
import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import get_settings
from app.database import close_db
from app.infrastructure.redis import close_redis_pool, get_redis_pool
from app.infrastructure.shutdown import get_shutdown_manager
from app.services.container.gc import ContainerGarbageCollector
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.container.warm_pool import WarmPoolManager

logger = structlog.get_logger(__name__)


async def _init_container_stack(app: FastAPI, settings) -> tuple:
    """
    コンテナ隔離スタックを初期化

    Returns:
        (docker_client, redis, orchestrator, gc)
    """
    docker_client = aiodocker.Docker(url=settings.docker_socket_path)
    logger.info("Dockerクライアント初期化完了", socket=settings.docker_socket_path)

    redis_pool = await get_redis_pool()
    redis = Redis(connection_pool=redis_pool)

    lifecycle = ContainerLifecycleManager(docker_client)
    warm_pool = WarmPoolManager(lifecycle, redis)
    orchestrator = ContainerOrchestrator(lifecycle, warm_pool, redis)

    # アプリケーション状態に保存（APIエンドポイントから参照）
    app.state.orchestrator = orchestrator
    app.state.docker_client = docker_client

    # GC（ガベージコレクター）- Proxy停止コールバックを渡す（BUG-12修正）
    gc = ContainerGarbageCollector(
        lifecycle,
        redis,
        proxy_stop_callback=orchestrator._stop_proxy,
    )
    app.state.gc = gc

    # WarmPoolプリヒート
    try:
        created = await warm_pool.preheat()
        pool_size = await warm_pool.get_pool_size()
        logger.info("WarmPoolプリヒート完了", pool_size=pool_size, created=created)
    except Exception as e:
        logger.error("WarmPoolプリヒートエラー", error=str(e))

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

    return docker_client, redis, orchestrator, gc


def _log_security_status(settings) -> None:
    """セキュリティ設定のログ出力"""
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


async def _shutdown_container_stack(
    docker_client, redis, orchestrator, gc
) -> None:
    """コンテナスタックのシャットダウン"""
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
        logger.debug("シャットダウンクリーンアップ失敗", exc_info=True)


async def _shutdown_resources() -> None:
    """共通リソースのシャットダウン"""
    try:
        await close_db()
    except Exception as e:
        logger.error("DBクローズエラー", error=str(e))

    try:
        await close_redis_pool()
    except Exception as e:
        logger.error("Redisクローズエラー", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    アプリケーションのライフサイクル管理

    コンテナ隔離アーキテクチャ:
      - aiodocker → ContainerLifecycleManager
      - WarmPoolManager（プレウォーム済みコンテナプール）
      - ContainerOrchestrator（会話→コンテナマッピング）
      - ContainerGarbageCollector（TTL超過コンテナ回収）
    """
    from app import __version__

    settings = get_settings()
    shutdown_manager = get_shutdown_manager()

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

    # コンテナ隔離スタック初期化
    docker_client, redis, orchestrator, gc = await _init_container_stack(
        app, settings
    )

    _log_security_status(settings)

    logger.info(
        "アプリケーション起動完了",
        environment=settings.app_env,
        port=settings.app_port,
    )

    yield

    # ---- 終了時 ----
    logger.info("アプリケーション終了中...")

    await shutdown_manager.graceful_shutdown()
    await _shutdown_container_stack(docker_client, redis, orchestrator, gc)
    await _shutdown_resources()

    logger.info("アプリケーション終了完了")
