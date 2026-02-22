"""
アプリケーションライフサイクル管理
起動時・終了時の処理を定義

CONTAINER_MANAGER_TYPE 環境変数で docker / ecs を切替。
"""
import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import get_settings
from app.database import close_db
from app.infrastructure.redis import close_redis_pool, get_redis_pool
from app.infrastructure.shutdown import get_shutdown_manager
from app.services.container.base import ContainerManagerBase
from app.services.container.gc import ContainerGarbageCollector
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.container.warm_pool import WarmPoolManager

logger = structlog.get_logger(__name__)


async def _recover_skills_from_s3(settings) -> None:
    """
    起動時にローカルのスキルディレクトリが空の場合、S3から復元する。

    正常な再起動（volume維持）ではスキップされる。
    volume消失後の再起動でのみ実行される。
    """
    if not settings.s3_skills_backup_enabled:
        logger.info("S3スキルバックアップ無効（復元スキップ）")
        return

    if not settings.s3_bucket_name:
        logger.debug("S3バケット未設定（スキル復元スキップ）")
        return

    from pathlib import Path

    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models.agent_skill import AgentSkill
    from app.services.skill_s3_backup import SkillS3Backup

    try:
        # DBからスキルが存在するテナントIDを取得
        async with async_session_maker() as db:
            result = await db.execute(
                select(AgentSkill.tenant_id).distinct()
            )
            tenant_ids = [row[0] for row in result.all()]

        if not tenant_ids:
            logger.debug("スキルレコードなし（復元スキップ）")
            return

        base_path = Path(settings.skills_base_path)
        backup = SkillS3Backup()
        total_restored = 0

        for tenant_id in tenant_ids:
            tenant_skills_path = (
                base_path / f"tenant_{tenant_id}" / ".claude" / "skills"
            )

            # ローカルにファイルが存在する場合はスキップ
            has_local_files = (
                tenant_skills_path.exists()
                and any(tenant_skills_path.rglob("*"))
            )
            if has_local_files:
                logger.debug(
                    "ローカルスキルあり（復元スキップ）",
                    tenant_id=tenant_id,
                )
                continue

            # S3から復元
            logger.info(
                "S3からスキル復元開始",
                tenant_id=tenant_id,
            )
            restored = await backup.restore_tenant_skills(
                tenant_id, base_path
            )
            total_restored += restored

        if total_restored > 0:
            logger.info(
                "S3スキル復元完了",
                total_restored=total_restored,
                tenant_count=len(tenant_ids),
            )

    except Exception as e:
        logger.error(
            "S3スキル復元エラー（起動は継続）",
            error=str(e),
        )


def _create_container_manager(settings, redis: Redis) -> tuple[ContainerManagerBase, object | None]:
    """container_manager_type に応じたマネージャーを生成

    Returns:
        (lifecycle, docker_client) — ECSモードでは docker_client=None
    """
    if settings.container_manager_type == "ecs":
        from app.services.container.ecs_manager import EcsContainerManager
        lifecycle = EcsContainerManager(redis)
        logger.info(
            "ECSコンテナマネージャー初期化完了",
            cluster=settings.ecs_cluster,
            task_definition=settings.ecs_task_definition,
        )
        return lifecycle, None
    else:
        import aiodocker
        from app.services.container.lifecycle import DockerContainerManager
        docker_client = aiodocker.Docker(url=settings.docker_socket_path)
        lifecycle = DockerContainerManager(docker_client)
        logger.info("Dockerコンテナマネージャー初期化完了", socket=settings.docker_socket_path)
        return lifecycle, docker_client


async def _init_container_stack(app: FastAPI, settings) -> tuple:
    """
    コンテナ隔離スタックを初期化

    Returns:
        (docker_client, redis, orchestrator, gc)
        ※ ECSモードでは docker_client=None
    """
    redis_pool = await get_redis_pool()
    redis = Redis(connection_pool=redis_pool)

    lifecycle, docker_client = _create_container_manager(settings, redis)

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

    manager_type = settings.container_manager_type
    logger.info(
        "コンテナ隔離スタック初期化完了",
        manager_type=manager_type,
        warm_pool_min=settings.ecs_warm_pool_min_size if manager_type == "ecs" else settings.warm_pool_min_size,
        warm_pool_max=settings.ecs_warm_pool_max_size if manager_type == "ecs" else settings.warm_pool_max_size,
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

    # Dockerクライアントクローズ（Docker モードのみ）
    if docker_client is not None:
        try:
            await docker_client.close()
            logger.info("Dockerクライアントクローズ完了")
        except Exception as e:
            logger.error("Dockerクライアントクローズエラー", error=str(e))

    # ECSクライアントクローズ
    lifecycle = orchestrator.lifecycle
    if hasattr(lifecycle, "close"):
        try:
            await lifecycle.close()
            logger.info("ECSクライアントクローズ完了")
        except Exception as e:
            logger.error("ECSクライアントクローズエラー", error=str(e))

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
      - CONTAINER_MANAGER_TYPE=docker → aiodocker + DockerContainerManager
      - CONTAINER_MANAGER_TYPE=ecs → aiobotocore + EcsContainerManager
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
        container_manager_type=settings.container_manager_type,
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

    # S3からスキル復元（ローカルが空の場合のみ）
    await _recover_skills_from_s3(settings)

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
