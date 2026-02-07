"""
コンテナ ガベージコレクター
TTL超過・不健全なコンテナを定期的に検出・破棄する
"""
import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from redis.asyncio import Redis

from app.config import get_settings
from app.services.container.config import REDIS_KEY_CONTAINER
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.container.models import ContainerInfo, ContainerStatus

logger = structlog.get_logger(__name__)
settings = get_settings()


class ContainerGarbageCollector:
    """コンテナGCループ"""

    def __init__(
        self,
        lifecycle: ContainerLifecycleManager,
        redis: Redis,
    ) -> None:
        self.lifecycle = lifecycle
        self.redis = redis
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
            except asyncio.CancelledError:
                break
            except Exception as e:
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
                str_data = {
                    k.decode("utf-8") if isinstance(k, bytes) else k:
                    v.decode("utf-8") if isinstance(v, bytes) else v
                    for k, v in redis_data.items()
                }
                info = ContainerInfo.from_redis_hash(str_data)

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
                state = container_info.get("State", {})
                if not state.get("Running", False):
                    logger.warning("GC: 孤立コンテナ破棄", container_id=container_id)
                    await self.lifecycle.destroy_container(container_id, grace_period=5)
                    destroyed_count += 1

        if destroyed_count > 0:
            logger.info("GCサイクル完了", destroyed=destroyed_count)

    def _should_destroy(self, info: ContainerInfo) -> bool:
        """コンテナを破棄すべきかどうか判定"""
        now = datetime.now(timezone.utc)

        # 非アクティブTTL
        inactive_ttl = timedelta(seconds=settings.container_inactive_ttl)
        if (now - info.last_active_at) > inactive_ttl:
            return True

        # 絶対TTL
        absolute_ttl = timedelta(seconds=settings.container_absolute_ttl)
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

            # コンテナ破棄
            await self.lifecycle.destroy_container(
                info.id, grace_period=settings.container_grace_period
            )

            # Redis メタデータ削除
            await self.redis.delete(f"{REDIS_KEY_CONTAINER}:{info.conversation_id}")

        except Exception as e:
            logger.error(
                "GC: グレースフル破棄失敗",
                container_id=info.id,
                error=str(e),
            )
