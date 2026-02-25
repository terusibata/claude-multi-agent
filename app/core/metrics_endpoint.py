"""
メトリクスエンドポイント
Prometheus形式でメトリクスを公開
"""
import os

import structlog
from fastapi.responses import PlainTextResponse

logger = structlog.get_logger(__name__)

from app.database import get_pool_status
from app.infrastructure.metrics import (
    get_db_pool_gauge,
    get_metrics_registry,
    get_workspace_host_cpu_percent,
)


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
        logger.debug("メトリクス収集失敗", target="db_pool", exc_info=True)

    # ホストCPUメトリクス更新
    try:
        load = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        get_workspace_host_cpu_percent().set(round(load[0] / cpu_count * 100, 1))
    except Exception:
        logger.debug("メトリクス収集失敗", target="host_cpu", exc_info=True)

    registry = get_metrics_registry()
    return PlainTextResponse(
        registry.export_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
