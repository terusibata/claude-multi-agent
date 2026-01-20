"""
Redis接続管理
分散ロック、キャッシュ、レート制限に使用
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import structlog
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError, RedisError

from app.config import get_settings

logger = structlog.get_logger(__name__)

settings = get_settings()

# グローバルRedisプール
_redis_pool: Optional[ConnectionPool] = None


async def get_redis_pool() -> ConnectionPool:
    """
    Redis接続プールを取得
    シングルトンパターンでプールを管理
    """
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
            socket_connect_timeout=5.0,
            socket_timeout=5.0,
            retry_on_timeout=True,
        )
        logger.info("Redis接続プール作成", url=settings.redis_url_masked)
    return _redis_pool


async def get_redis() -> AsyncGenerator[Redis, None]:
    """
    Redisクライアントを取得するDependency
    """
    pool = await get_redis_pool()
    redis = Redis(connection_pool=pool)
    try:
        yield redis
    finally:
        await redis.aclose()


@asynccontextmanager
async def redis_client() -> AsyncGenerator[Redis, None]:
    """
    Redisクライアントのコンテキストマネージャー
    """
    pool = await get_redis_pool()
    redis = Redis(connection_pool=pool)
    try:
        yield redis
    finally:
        await redis.aclose()


async def close_redis_pool() -> None:
    """
    Redis接続プールをクローズ
    アプリケーション終了時に呼び出す
    """
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
        logger.info("Redis接続プールクローズ")


async def check_redis_health() -> tuple[bool, Optional[str]]:
    """
    Redisの接続状態をチェック

    Returns:
        (healthy: bool, error_message: Optional[str])
    """
    try:
        async with redis_client() as redis:
            await redis.ping()
        return True, None
    except ConnectionError as e:
        return False, f"Redis connection error: {str(e)}"
    except RedisError as e:
        return False, f"Redis error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"
