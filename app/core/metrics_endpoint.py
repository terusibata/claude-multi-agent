"""
メトリクスエンドポイント
Prometheus形式でメトリクスを公開
"""
import os

from fastapi.responses import PlainTextResponse

from app.database import get_pool_status
from app.infrastructure.metrics import (
    get_active_connections,
    get_db_pool_gauge,
    get_metrics_registry,
    get_workspace_host_cpu_percent,
    get_workspace_warm_pool_size,
)
from app.infrastructure.redis import get_pool_info


async def metrics_handler(app_state) -> PlainTextResponse:
    """
    Prometheusメトリクスを収集し返す

    Args:
        app_state: FastAPIアプリケーションの状態
    """
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
            connections_gauge.set(
                redis_info.get("max_connections", 0), type="redis_max"
            )
    except Exception:
        pass

    # ワークスペースコンテナメトリクス更新
    try:
        orchestrator = getattr(app_state, "orchestrator", None)
        if orchestrator:
            pool_size = await orchestrator.warm_pool.get_pool_size()
            get_workspace_warm_pool_size().set(pool_size)
        load = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        get_workspace_host_cpu_percent().set(round(load[0] / cpu_count * 100, 1))
    except Exception:
        pass

    registry = get_metrics_registry()
    return PlainTextResponse(
        registry.export_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
