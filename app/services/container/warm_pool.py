"""
WarmPool マネージャー
事前起動済みコンテナのプールを管理し、高速なコンテナ割り当てを実現する
"""
import asyncio

import structlog
from redis.asyncio import Redis

from app.config import get_settings
from app.services.container.config import (
    REDIS_KEY_WARM_POOL,
    REDIS_KEY_WARM_POOL_INFO,
    WARM_POOL_TTL_SECONDS,
)
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.container.models import ContainerInfo

logger = structlog.get_logger(__name__)
settings = get_settings()


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

    async def acquire(self) -> ContainerInfo:
        """
        WarmPoolからコンテナを1つ取得

        LPOP でアトミックに取得するためマルチインスタンス間で競合しない。
        プール内のコンテナが不健全な場合はスキップして次を試行。
        プールが空の場合は新規作成。

        Returns:
            取得したコンテナ情報
        """
        while True:
            container_id = await self.redis.lpop(REDIS_KEY_WARM_POOL)
            if not container_id:
                break

            if isinstance(container_id, bytes):
                container_id = container_id.decode("utf-8")

            info = await self._get_pool_container_info(container_id)
            if info and await self.lifecycle.is_healthy(container_id):
                # プール情報を削除
                await self.redis.delete(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
                # 非同期で補充をスケジュール
                asyncio.create_task(self.replenish())
                logger.info("WarmPool: コンテナ取得", container_id=container_id)
                return info

            # 不健全なコンテナは破棄
            logger.warning("WarmPool: 不健全コンテナを破棄", container_id=container_id)
            await self.redis.delete(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
            asyncio.create_task(self._cleanup_unhealthy(container_id))

        # プール空 → 新規作成
        logger.info("WarmPool: プール空、新規作成")
        asyncio.create_task(self.replenish())
        return await self.lifecycle.create_container()

    async def replenish(self) -> None:
        """プールを最小サイズまで補充"""
        current_size = await self.redis.llen(REDIS_KEY_WARM_POOL)
        needed = self.min_size - current_size

        if needed <= 0:
            return

        logger.info("WarmPool: 補充開始", current=current_size, needed=needed)
        tasks = [self._create_and_add() for _ in range(needed)]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _create_and_add(self) -> None:
        """コンテナを1つ作成してプールに追加"""
        try:
            current_size = await self.redis.llen(REDIS_KEY_WARM_POOL)
            if current_size >= self.max_size:
                return

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
        except Exception as e:
            logger.error("WarmPool: コンテナ作成失敗", error=str(e))

    async def _get_pool_container_info(self, container_id: str) -> ContainerInfo | None:
        """Redisからプールコンテナの情報を取得"""
        data = await self.redis.hgetall(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
        if not data:
            return None
        # bytes → str 変換
        str_data = {
            k.decode("utf-8") if isinstance(k, bytes) else k:
            v.decode("utf-8") if isinstance(v, bytes) else v
            for k, v in data.items()
        }
        return ContainerInfo.from_redis_hash(str_data)

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
            if isinstance(container_id, bytes):
                container_id = container_id.decode("utf-8")
            await self.redis.delete(f"{REDIS_KEY_WARM_POOL_INFO}:{container_id}")
            try:
                await self.lifecycle.destroy_container(container_id, grace_period=5)
            except Exception as e:
                logger.error("WarmPool: ドレイン中のエラー", container_id=container_id, error=str(e))
        logger.info("WarmPool: ドレイン完了")
