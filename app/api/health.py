"""
ヘルスチェックエンドポイント

Kubernetes/ECS対応のヘルスチェック実装
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import get_settings
from app.database import get_db
from app.infrastructure.redis import check_redis_health

logger = structlog.get_logger(__name__)
settings = get_settings()


class HealthStatus(str, Enum):
    """ヘルスステータス"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    """コンポーネントヘルス"""
    status: HealthStatus
    message: Optional[str] = None
    latency_ms: Optional[float] = None


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス"""
    status: HealthStatus
    version: str
    environment: str
    timestamp: str
    checks: dict[str, ComponentHealth]


router = APIRouter(tags=["ヘルスチェック"])


async def check_database_health(db: AsyncSession) -> ComponentHealth:
    """データベースヘルスチェック"""
    import time
    start = time.perf_counter()

    try:
        await db.execute(text("SELECT 1"))
        latency = (time.perf_counter() - start) * 1000

        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            latency_ms=round(latency, 2),
        )
    except Exception as e:
        logger.error("データベースヘルスチェック失敗", error=str(e))
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            message=str(e),
        )


async def check_redis_component_health() -> ComponentHealth:
    """Redisヘルスチェック"""
    import time
    start = time.perf_counter()

    healthy, error, _latency = await check_redis_health()
    latency = (time.perf_counter() - start) * 1000

    if healthy:
        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            latency_ms=round(latency, 2),
        )
    else:
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            message=error,
        )


async def check_s3_health() -> ComponentHealth:
    """S3ヘルスチェック"""
    import time

    if not settings.s3_bucket_name:
        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            message="S3未設定（スキップ）",
        )

    start = time.perf_counter()

    try:
        import boto3
        from botocore.config import Config

        config = Config(
            connect_timeout=5,
            read_timeout=5,
            retries={"max_attempts": 1},
        )
        s3 = boto3.client("s3", config=config, region_name=settings.aws_region)
        s3.head_bucket(Bucket=settings.s3_bucket_name)
        latency = (time.perf_counter() - start) * 1000

        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            latency_ms=round(latency, 2),
        )
    except Exception as e:
        logger.error("S3ヘルスチェック失敗", error=str(e))
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            message=str(e),
        )


async def check_container_system_health() -> ComponentHealth:
    """コンテナ隔離システムのヘルスチェック"""
    import time

    start = time.perf_counter()

    try:
        from app.main import app as main_app

        orchestrator = getattr(main_app.state, "orchestrator", None)
        if orchestrator is None:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message="Orchestrator未初期化",
            )

        # WarmPoolサイズ確認
        pool_size = await orchestrator.warm_pool.get_pool_size()
        latency = (time.perf_counter() - start) * 1000

        if pool_size == 0:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"WarmPool空（補充中の可能性あり）",
                latency_ms=round(latency, 2),
            )

        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            message=f"WarmPool: {pool_size}コンテナ待機中",
            latency_ms=round(latency, 2),
        )
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        logger.error("コンテナシステムヘルスチェック失敗", error=str(e))
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            message=str(e),
            latency_ms=round(latency, 2),
        )


def determine_overall_status(checks: dict[str, ComponentHealth]) -> HealthStatus:
    """
    全体のヘルスステータスを判定

    - すべてhealthy: healthy
    - 重要コンポーネント（database）がunhealthy: unhealthy
    - その他がunhealthy: degraded
    """
    critical_components = {"database"}
    has_critical_failure = False
    has_non_critical_failure = False

    for name, health in checks.items():
        if health.status == HealthStatus.UNHEALTHY:
            if name in critical_components:
                has_critical_failure = True
            else:
                has_non_critical_failure = True

    if has_critical_failure:
        return HealthStatus.UNHEALTHY
    if has_non_critical_failure:
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="詳細ヘルスチェック",
    description="全コンポーネントの状態を確認するヘルスチェック",
)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """
    詳細ヘルスチェック

    データベース、Redis、S3の接続状態を確認します。
    """
    # 各コンポーネントのヘルスチェックを並列実行
    import asyncio

    db_health_task = check_database_health(db)
    redis_health_task = check_redis_component_health()
    container_health_task = check_container_system_health()

    db_health, redis_health, container_health = await asyncio.gather(
        db_health_task,
        redis_health_task,
        container_health_task,
    )

    # S3は同期APIなので別途実行
    s3_health = await check_s3_health()

    checks = {
        "database": db_health,
        "redis": redis_health,
        "s3": s3_health,
        "container_system": container_health,
    }

    overall_status = determine_overall_status(checks)

    return HealthResponse(
        status=overall_status,
        version=__version__,
        environment=settings.app_env,
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks=checks,
    )


@router.get(
    "/health/live",
    summary="Liveness Probe",
    description="Kubernetesのliveness probe用エンドポイント",
)
async def liveness_probe():
    """
    Liveness Probe

    アプリケーションが生存しているかを確認します。
    常に200を返します（プロセスが動作していれば成功）。
    """
    return {"status": "alive"}


@router.get(
    "/health/ready",
    summary="Readiness Probe",
    description="Kubernetesのreadiness probe用エンドポイント",
)
async def readiness_probe(db: AsyncSession = Depends(get_db)):
    """
    Readiness Probe

    アプリケーションがトラフィックを受け入れる準備ができているかを確認します。
    データベース接続が確立されている場合に200を返します。
    """
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        logger.error("Readiness check failed", error=str(e))
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Service not ready")
