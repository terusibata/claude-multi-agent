"""
WarmPool マネージャー
事前起動済みコンテナのプールを管理し、高速なコンテナ割り当てを実現する

Phase 2 改善:
  - 起動時プリヒート（min_sizeまで非同期充填）
  - 補充リトライ（exponential backoff, 最大3回）
  - Prometheusメトリクス収集（枯渇回数、ヒット率、取得レイテンシ）
  - 設定ホットリロード（Redis経由で min/max_size 動的変更）
"""
import asyncio
import time

import structlog
from redis.asyncio import Redis

from app.config import get_settings
from app.infrastructure.metrics import (
    get_workspace_warm_pool_acquire,
    get_workspace_warm_pool_exhausted,
    get_workspace_warm_pool_size,
)
from app.services.container.config import (
    REDIS_KEY_WARM_POOL,
    REDIS_KEY_WARM_POOL_INFO,
    WARM_POOL_TTL_SECONDS,
)
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.container.models import ContainerInfo

logger = structlog.get_logger(__name__)
settings = get_settings()

# Redis keys for hot-reload config
REDIS_KEY_WARM_POOL_CONFIG = "workspace:warm_pool:config"
_REPLENISH_MAX_RETRIES = 3
_REPLENISH_BASE_DELAY = 2.0  # seconds


class WarmPoolManager:
    """事前起動コンテナプール"""

    def __init__(
        self,
        lifecycle: ContainerLifecycleManager,
        redis: Redis,
        min_size: int | None = None,
        max_size: int | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.redis = redis
        self.min_size = min_size or settings.warm_pool_min_size
        self.max_size = max_size or settings.warm_pool_max_size

    async def preheat(self) -> int:
        """
        起動時プリヒート: min_sizeまでプールを充填

        Returns:
            プリヒートで作成したコンテナ数
        """
        current_size = await self.redis.llen(REDIS_KEY_WARM_POOL)
        needed = self.min_size - current_size
        if needed <= 0:
            logger.info("WarmPool: プリヒート不要", current=current_size, min=self.min_size)
            return 0

        logger.info("WarmPool: プリヒート開始", current=current_size, needed=needed)
        tasks = [self._create_and_add_with_retry() for _ in range(needed)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        created = sum(1 for r in results if r is True)
        self._update_pool_size_metric()
        logger.info("WarmPool: プリヒート完了", created=created, failed=needed - created)
        return created

    async def acquire(self) -> ContainerInfo:
        """
        WarmPoolからコンテナを1つ取得

        LPOP でアトミックに取得するためマルチインスタンス間で競合しない。
        プール内のコンテナが不健全な場合はスキップして次を試行。
        プールが空の場合は新規作成。

        Returns:
            取得したコンテナ情報
        """
        start_time = time.perf_counter()
        acquire_histogram = get_workspace_warm_pool_acquire()

        # ホットリロード: Redis経由でmin/max_sizeを更新
        await self._reload_config()

        while True:
            container_id = await self.redis.lpop(REDIS_KEY_WARM_POOL)
            if not container_id:
                break

            info = await self._get_pool_container_info(container_id)
            if info and await self.lifecycle.is_healthy(container_id):
                # プール情報を削除
                await self.redis.delete(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
                # 非同期で補充をスケジュール
                asyncio.create_task(self.replenish())
                self._update_pool_size_metric()
                duration = time.perf_counter() - start_time
                acquire_histogram.observe(duration)
                logger.info("WarmPool: コンテナ取得", container_id=container_id, duration_ms=round(duration * 1000, 1))
                return info

            # 不健全なコンテナは破棄
            logger.warning("WarmPool: 不健全コンテナを破棄", container_id=container_id)
            await self.redis.delete(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
            asyncio.create_task(self._cleanup_unhealthy(container_id))

        # プール空 → 枯渇メトリクス記録 + 新規作成
        get_workspace_warm_pool_exhausted().inc()
        logger.warning("WarmPool: プール枯渇、新規作成にフォールバック")
        asyncio.create_task(self.replenish())

        info = await self.lifecycle.create_container()
        self._update_pool_size_metric()
        duration = time.perf_counter() - start_time
        acquire_histogram.observe(duration)
        return info

    async def replenish(self) -> None:
        """プールを最小サイズまで補充"""
        await self._reload_config()
        current_size = await self.redis.llen(REDIS_KEY_WARM_POOL)
        needed = self.min_size - current_size

        if needed <= 0:
            return

        logger.info("WarmPool: 補充開始", current=current_size, needed=needed)
        tasks = [self._create_and_add_with_retry() for _ in range(needed)]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._update_pool_size_metric()

    async def _create_and_add_with_retry(self) -> bool:
        """
        コンテナを1つ作成してプールに追加（exponential backoffリトライ付き）

        Returns:
            成功した場合True
        """
        for attempt in range(_REPLENISH_MAX_RETRIES):
            try:
                current_size = await self.redis.llen(REDIS_KEY_WARM_POOL)
                if current_size >= self.max_size:
                    return False

                info = await self.lifecycle.create_container()
                # Redis に情報保存
                await self.redis.hset(
                    f"{REDIS_KEY_WARM_POOL_INFO}:{info.id}",
                    mapping=info.to_redis_hash(),
                )
                await self.redis.expire(
                    f"{REDIS_KEY_WARM_POOL_INFO}:{info.id}",
                    WARM_POOL_TTL_SECONDS,
                )
                await self.redis.rpush(REDIS_KEY_WARM_POOL, info.id)
                logger.info("WarmPool: コンテナ追加", container_id=info.id)
                return True
            except Exception as e:
                delay = _REPLENISH_BASE_DELAY * (2 ** attempt)
                logger.error(
                    "WarmPool: コンテナ作成失敗",
                    error=str(e),
                    attempt=attempt + 1,
                    max_retries=_REPLENISH_MAX_RETRIES,
                    retry_delay=delay,
                )
                if attempt < _REPLENISH_MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
        return False

    async def _get_pool_container_info(self, container_id: str) -> ContainerInfo | None:
        """Redisからプールコンテナの情報を取得"""
        data = await self.redis.hgetall(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
        if not data:
            return None
        return ContainerInfo.from_redis_hash(data)

    async def _cleanup_unhealthy(self, container_id: str) -> None:
        """不健全なコンテナを破棄"""
        try:
            await self.lifecycle.destroy_container(container_id, grace_period=5)
        except Exception as e:
            logger.error("WarmPool: 不健全コンテナ破棄失敗", container_id=container_id, error=str(e))

    async def get_pool_size(self) -> int:
        """現在のプールサイズを取得"""
        return await self.redis.llen(REDIS_KEY_WARM_POOL)

    async def drain(self) -> None:
        """プール内の全コンテナを破棄（シャットダウン時）"""
        logger.info("WarmPool: ドレイン開始")
        while True:
            container_id = await self.redis.lpop(REDIS_KEY_WARM_POOL)
            if not container_id:
                break
            await self.redis.delete(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
            try:
                await self.lifecycle.destroy_container(container_id, grace_period=5)
            except Exception as e:
                logger.error("WarmPool: ドレイン中のエラー", container_id=container_id, error=str(e))
        self._update_pool_size_metric()
        logger.info("WarmPool: ドレイン完了")

    async def update_config(self, min_size: int | None = None, max_size: int | None = None) -> None:
        """
        WarmPool設定をRedis経由で動的更新（ホットリロード用）

        Args:
            min_size: 新しい最小プールサイズ
            max_size: 新しい最大プールサイズ
        """
        config_update: dict[str, str] = {}
        if min_size is not None:
            config_update["min_size"] = str(min_size)
        if max_size is not None:
            config_update["max_size"] = str(max_size)

        if config_update:
            await self.redis.hset(REDIS_KEY_WARM_POOL_CONFIG, mapping=config_update)
            await self._reload_config()
            logger.info("WarmPool: 設定更新", min_size=self.min_size, max_size=self.max_size)

    async def _reload_config(self) -> None:
        """Redis経由で設定をリロード"""
        try:
            config = await self.redis.hgetall(REDIS_KEY_WARM_POOL_CONFIG)
            if not config:
                return
            if "min_size" in config:
                self.min_size = int(config["min_size"])
            if "max_size" in config:
                self.max_size = int(config["max_size"])
        except Exception:
            pass

    def _update_pool_size_metric(self) -> None:
        """プールサイズメトリクスを非同期更新"""
        asyncio.create_task(self._async_update_pool_size_metric())

    async def _async_update_pool_size_metric(self) -> None:
        """プールサイズメトリクスを更新"""
        try:
            size = await self.redis.llen(REDIS_KEY_WARM_POOL)
            get_workspace_warm_pool_size().set(size)
        except Exception:
            pass
