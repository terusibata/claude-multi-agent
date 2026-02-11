"""
Redis接続管理
分散ロック、キャッシュ、レート制限に使用
"""
import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError, RedisError

from app.config import get_settings

logger = structlog.get_logger(__name__)

settings = get_settings()

# グローバルRedisプール
_redis_pool: ConnectionPool | None = None
_redis_pool_lock = asyncio.Lock()


async def get_redis_pool() -> ConnectionPool:
    """
    Redis接続プールを取得
    シングルトンパターンでプールを管理（排他制御付き）
    """
    global _redis_pool
    if _redis_pool is not None:
        return _redis_pool

    async with _redis_pool_lock:
        # ダブルチェックロック
        if _redis_pool is not None:
            return _redis_pool

        _redis_pool = ConnectionPool.from_url(
            settings.redis_url_with_auth,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout,
            socket_timeout=settings.redis_socket_timeout,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        logger.info(
            "Redis接続プール作成",
            url=settings.redis_url_masked,
            max_connections=settings.redis_max_connections,
        )
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
        logger.info("Redis接続プールをクローズ中...")
        await _redis_pool.aclose()
        _redis_pool = None
        logger.info("Redis接続プールクローズ完了")


async def check_redis_health() -> tuple[bool, str | None, float]:
    """
    Redisの接続状態をチェック

    Returns:
        (healthy: bool, error_message: str | None, latency_ms: float)
    """
    start = time.perf_counter()
    try:
        async with redis_client() as redis:
            await redis.ping()
        latency = (time.perf_counter() - start) * 1000
        return True, None, latency
    except ConnectionError as e:
        latency = (time.perf_counter() - start) * 1000
        return False, f"Redis connection error: {str(e)}", latency
    except RedisError as e:
        latency = (time.perf_counter() - start) * 1000
        return False, f"Redis error: {str(e)}", latency
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return False, f"Unexpected error: {str(e)}", latency


def get_pool_info() -> dict:
    """
    Redisプールの情報を取得

    Returns:
        プール情報の辞書
    """
    global _redis_pool
    if _redis_pool is None:
        return {"initialized": False}

    return {
        "initialized": True,
        "max_connections": _redis_pool.max_connections,
    }
