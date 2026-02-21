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
from app.services.container.config import (
    REDIS_KEY_CONTAINER,
    REDIS_KEY_CONTAINER_REVERSE,
    REDIS_KEY_WARM_POOL_INFO,
)
from app.services.container.base import ContainerManagerBase
from app.services.container.models import ContainerInfo, ContainerStatus

logger = structlog.get_logger(__name__)

# 孤立コンテナの最小経過時間（作成直後の正常コンテナを誤回収しない）
_ORPHAN_MIN_AGE_SECONDS = 300


class ContainerGarbageCollector:
    """コンテナGCループ"""

    def __init__(
        self,
        lifecycle: ContainerManagerBase,
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
        """1サイクルのGC実行

        Dockerモード: Docker APIからコンテナ一覧を取得してGC。
        ECSモード: Redis SCANでコンテナ一覧を取得してGC。
                  + N回に1回、ListTasksと照合して孤立タスクを検出。
        """
        if self._settings.container_manager_type == "ecs":
            await self._collect_ecs()
        else:
            await self._collect_docker()

    async def _collect_docker(self) -> None:
        """Dockerモード: Docker APIベースのGC"""
        destroyed_count = 0

        containers = await self.lifecycle.list_workspace_containers()

        for container_info in containers:
            container_id = container_info.get("Name", "").lstrip("/")
            labels = container_info.get("Config", {}).get("Labels", {})
            conversation_id = labels.get("workspace.conversation_id", "")

            if not container_id:
                continue

            pool_info_exists = await self.redis.exists(
                f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}"
            )
            if pool_info_exists:
                continue

            if not conversation_id:
                mapped_id = await self.redis.get(
                    f"{REDIS_KEY_CONTAINER_REVERSE}:{container_id}"
                )
                if mapped_id:
                    conversation_id = mapped_id

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
                created_str = container_info.get("Created", "")
                is_old_enough = True
                if created_str:
                    try:
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

    _ecs_gc_cycle_count: int = 0

    async def _collect_ecs(self) -> None:
        """ECSモード: Redis SCANベースのGC"""
        self._ecs_gc_cycle_count += 1
        destroyed_count = 0

        # Redis SCANでワークスペースコンテナ一覧を取得
        cursor = 0
        container_keys: list[str] = []
        while True:
            cursor, keys = await self.redis.scan(
                cursor, match=f"{REDIS_KEY_CONTAINER}:*", count=100,
            )
            container_keys.extend(keys)
            if cursor == 0:
                break

        for key in container_keys:
            redis_data = await self.redis.hgetall(key)
            if not redis_data:
                continue

            info = ContainerInfo.from_redis_hash(redis_data)

            # WarmPool管理のコンテナはスキップ
            pool_info_exists = await self.redis.exists(
                f"{REDIS_KEY_WARM_POOL_INFO}:{info.id}"
            )
            if pool_info_exists:
                continue

            if self._should_destroy(info):
                logger.info(
                    "GC(ECS): コンテナ破棄対象",
                    container_id=info.id,
                    conversation_id=info.conversation_id,
                    status=info.status.value,
                )
                await self._graceful_destroy(info)
                destroyed_count += 1

        # 5サイクルに1回: ECSタスクとRedisを照合して孤立タスクを検出
        if self._ecs_gc_cycle_count % 5 == 0:
            orphan_count = await self._detect_orphan_ecs_tasks()
            destroyed_count += orphan_count

        if destroyed_count > 0:
            logger.info("GCサイクル完了(ECS)", destroyed=destroyed_count)

    async def _detect_orphan_ecs_tasks(self) -> int:
        """ECSタスクのうちRedisに記録がないものを検出・停止"""
        destroyed = 0
        try:
            containers = await self.lifecycle.list_workspace_containers()
            for c in containers:
                container_id = c.get("Name", "")
                if not container_id:
                    continue

                # Redisに逆引きキーがあるか確認
                has_reverse = await self.redis.exists(
                    f"{REDIS_KEY_CONTAINER_REVERSE}:{container_id}"
                )
                has_pool = await self.redis.exists(
                    f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}"
                )
                has_ecs_task = await self.redis.exists(
                    f"workspace:ecs_task:{container_id}"
                )

                if not has_reverse and not has_pool and not has_ecs_task:
                    logger.warning(
                        "GC(ECS): 孤立タスク検出",
                        container_id=container_id,
                    )
                    try:
                        await self.lifecycle.destroy_container(container_id, grace_period=5)
                        get_workspace_active_containers().dec()
                        destroyed += 1
                    except Exception as e:
                        logger.error(
                            "GC(ECS): 孤立タスク破棄失敗",
                            container_id=container_id,
                            error=str(e),
                        )
        except Exception as e:
            logger.error("GC(ECS): 孤立タスク検出エラー", error=str(e))

        return destroyed

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

            # Redis メタデータ削除（正引き + 逆引き + ECSタスクマッピング）
            await self.redis.delete(f"{REDIS_KEY_CONTAINER}:{info.conversation_id}")
            await self.redis.delete(f"{REDIS_KEY_CONTAINER_REVERSE}:{info.id}")
            if info.manager_type == "ecs":
                await self.redis.delete(f"workspace:ecs_task:{info.id}")

            # BUG-13修正: アクティブコンテナメトリクスをデクリメント
            get_workspace_active_containers().dec()

        except Exception as e:
            logger.error(
                "GC: グレースフル破棄失敗",
                container_id=info.id,
                error=str(e),
            )
