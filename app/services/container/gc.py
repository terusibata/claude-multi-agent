"""
コンテナ ガベージコレクター
TTL超過・不健全なコンテナを定期的に検出・破棄する
"""
import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import structlog
from redis.asyncio import Redis

from app.config import get_settings
from app.infrastructure.metrics import get_workspace_active_containers, get_workspace_gc_cycles
from app.services.container.config import REDIS_KEY_CONTAINER
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.container.models import ContainerInfo, ContainerStatus

logger = structlog.get_logger(__name__)

# 孤立コンテナの最小経過時間（作成直後の正常コンテナを誤回収しない）
_ORPHAN_MIN_AGE_SECONDS = 300


class ContainerGarbageCollector:
    """コンテナGCループ"""

    def __init__(
        self,
        lifecycle: ContainerLifecycleManager,
        redis: Redis,
        proxy_stop_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.redis = redis
        self._settings = get_settings()
        # Orchestrator由来のProxy停止コールバック（BUG-12修正）
        self._proxy_stop_callback = proxy_stop_callback
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self, interval: int = 60) -> None:
        """GCループを開始"""
        self._running = True
        self._task = asyncio.create_task(self._gc_loop(interval))
        logger.info("GC開始", interval=interval)

    async def stop(self) -> None:
        """GCループを停止"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("GC停止")

    async def _gc_loop(self, interval: int) -> None:
        """GCメインループ"""
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._collect()
                get_workspace_gc_cycles().inc(result="success")
            except asyncio.CancelledError:
                break
            except Exception as e:
                get_workspace_gc_cycles().inc(result="error")
                logger.error("GCサイクルエラー", error=str(e))

    async def _collect(self) -> None:
        """1サイクルのGC実行"""
        destroyed_count = 0

        # Docker APIからワークスペースコンテナ一覧を取得
        containers = await self.lifecycle.list_workspace_containers()

        for container_info in containers:
            container_id = container_info.get("Name", "").lstrip("/")
            labels = container_info.get("Config", {}).get("Labels", {})
            conversation_id = labels.get("workspace.conversation_id", "")

            if not container_id:
                continue

            # Redis からコンテナメタデータ取得
            redis_data = await self.redis.hgetall(
                f"{REDIS_KEY_CONTAINER}:{conversation_id}"
            )

            if redis_data:
                info = ContainerInfo.from_redis_hash(redis_data)

                if self._should_destroy(info):
                    logger.info(
                        "GC: コンテナ破棄対象",
                        container_id=container_id,
                        conversation_id=conversation_id,
                        status=info.status.value,
                    )
                    await self._graceful_destroy(info)
                    destroyed_count += 1
            else:
                # Redisにメタデータがないコンテナ（孤立コンテナ）
                # 作成から一定時間経過したものは状態に関わらず回収
                created_str = container_info.get("Created", "")
                is_old_enough = True
                if created_str:
                    try:
                        # Docker APIのCreatedはUnixタイムスタンプ（int/float）
                        created_ts = float(created_str) if isinstance(created_str, (int, float, str)) else 0
                        created_at = datetime.fromtimestamp(created_ts, tz=timezone.utc)
                        age = (datetime.now(timezone.utc) - created_at).total_seconds()
                        is_old_enough = age > _ORPHAN_MIN_AGE_SECONDS
                    except (ValueError, TypeError, OSError):
                        is_old_enough = True

                if is_old_enough:
                    logger.warning("GC: 孤立コンテナ破棄", container_id=container_id)
                    if self._proxy_stop_callback:
                        try:
                            await self._proxy_stop_callback(container_id)
                        except Exception:
                            logger.warning("GCプロキシ停止失敗", exc_info=True)
                    await self.lifecycle.destroy_container(container_id, grace_period=5)
                    get_workspace_active_containers().dec()
                    destroyed_count += 1

        if destroyed_count > 0:
            logger.info("GCサイクル完了", destroyed=destroyed_count)

    def _should_destroy(self, info: ContainerInfo) -> bool:
        """コンテナを破棄すべきかどうか判定"""
        now = datetime.now(timezone.utc)

        # 非アクティブTTL
        inactive_ttl = timedelta(seconds=self._settings.container_inactive_ttl)
        if (now - info.last_active_at) > inactive_ttl:
            return True

        # 絶対TTL
        absolute_ttl = timedelta(seconds=self._settings.container_absolute_ttl)
        if (now - info.created_at) > absolute_ttl:
            return True

        # draining状態
        if info.status == ContainerStatus.DRAINING:
            return True

        return False

    async def _graceful_destroy(self, info: ContainerInfo) -> None:
        """コンテナをグレースフルに破棄"""
        try:
            # Redis: status → draining
            await self.redis.hset(
                f"{REDIS_KEY_CONTAINER}:{info.conversation_id}",
                "status",
                ContainerStatus.DRAINING.value,
            )

            # BUG-12修正: Proxy停止（リークを防ぐ）
            if self._proxy_stop_callback:
                try:
                    await self._proxy_stop_callback(info.id)
                except Exception as e:
                    logger.warning("GC: Proxy停止エラー", container_id=info.id, error=str(e))

            # コンテナ破棄
            await self.lifecycle.destroy_container(
                info.id, grace_period=self._settings.container_grace_period
            )

            # Redis メタデータ削除
            await self.redis.delete(f"{REDIS_KEY_CONTAINER}:{info.conversation_id}")

            # BUG-13修正: アクティブコンテナメトリクスをデクリメント
            get_workspace_active_containers().dec()

        except Exception as e:
            logger.error(
                "GC: グレースフル破棄失敗",
                container_id=info.id,
                error=str(e),
            )
