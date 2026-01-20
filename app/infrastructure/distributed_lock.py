"""
分散ロック機構

Redisを使用した分散ロックの実装
水平スケーリング環境での同時実行制御
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.infrastructure.redis import redis_client

logger = structlog.get_logger(__name__)


class DistributedLockError(Exception):
    """分散ロックエラー基底クラス"""

    def __init__(self, resource_id: str, message: str):
        self.resource_id = resource_id
        self.message = message
        super().__init__(f"{message}: {resource_id}")


class LockAcquisitionError(DistributedLockError):
    """ロック取得失敗エラー"""
    pass


class LockReleaseError(DistributedLockError):
    """ロック解放失敗エラー"""
    pass


class ConversationLockError(DistributedLockError):
    """会話ロック取得エラー（後方互換性のため）"""
    pass


class DistributedLockManager:
    """
    Redis分散ロックマネージャー

    Redlock風の実装だが、単一Redisインスタンス用に簡略化
    本番環境でRedisクラスターを使用する場合は拡張可能
    """

    # ロックキーのプレフィックス
    LOCK_PREFIX = "lock:"

    # デフォルトタイムアウト（秒）
    DEFAULT_LOCK_TTL = 600  # 10分
    DEFAULT_ACQUIRE_TIMEOUT = 5.0  # 5秒
    DEFAULT_RETRY_INTERVAL = 0.1  # 100ms

    # Luaスクリプト: アトミックなロック解放
    # ロックトークンが一致する場合のみ削除
    RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    # Luaスクリプト: ロック延長
    EXTEND_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("pexpire", KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    def __init__(self, key_prefix: str = ""):
        """
        初期化

        Args:
            key_prefix: ロックキーの追加プレフィックス
        """
        self.key_prefix = key_prefix

    def _make_lock_key(self, resource_id: str) -> str:
        """ロックキーを生成"""
        return f"{self.LOCK_PREFIX}{self.key_prefix}{resource_id}"

    def _generate_token(self) -> str:
        """ユニークなロックトークンを生成"""
        return str(uuid.uuid4())

    async def acquire(
        self,
        resource_id: str,
        ttl: int = DEFAULT_LOCK_TTL,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
        retry_interval: float = DEFAULT_RETRY_INTERVAL,
    ) -> str:
        """
        分散ロックを取得

        Args:
            resource_id: リソースID
            ttl: ロックの有効期限（秒）
            acquire_timeout: ロック取得の最大待機時間（秒）
            retry_interval: リトライ間隔（秒）

        Returns:
            ロックトークン（解放時に必要）

        Raises:
            LockAcquisitionError: ロック取得に失敗した場合
        """
        lock_key = self._make_lock_key(resource_id)
        token = self._generate_token()
        ttl_ms = ttl * 1000

        start_time = asyncio.get_event_loop().time()
        end_time = start_time + acquire_timeout

        async with redis_client() as redis:
            while asyncio.get_event_loop().time() < end_time:
                try:
                    # SET NX PX でアトミックにロック取得を試みる
                    acquired = await redis.set(
                        lock_key,
                        token,
                        nx=True,  # キーが存在しない場合のみ設定
                        px=ttl_ms,  # ミリ秒単位のTTL
                    )

                    if acquired:
                        logger.debug(
                            "分散ロック取得",
                            resource_id=resource_id,
                            lock_key=lock_key,
                            ttl=ttl,
                        )
                        return token

                    # ロック取得失敗、リトライ
                    await asyncio.sleep(retry_interval)

                except RedisError as e:
                    logger.error(
                        "Redis操作エラー",
                        resource_id=resource_id,
                        error=str(e),
                    )
                    raise LockAcquisitionError(
                        resource_id,
                        f"Redisエラーによりロック取得に失敗: {str(e)}"
                    )

        # タイムアウト
        raise LockAcquisitionError(
            resource_id,
            f"ロック取得がタイムアウトしました（{acquire_timeout}秒）"
        )

    async def release(self, resource_id: str, token: str) -> bool:
        """
        分散ロックを解放

        Args:
            resource_id: リソースID
            token: ロック取得時に返されたトークン

        Returns:
            解放成功かどうか
        """
        lock_key = self._make_lock_key(resource_id)

        async with redis_client() as redis:
            try:
                # Luaスクリプトでアトミックに解放
                result = await redis.eval(
                    self.RELEASE_SCRIPT,
                    1,
                    lock_key,
                    token,
                )

                if result == 1:
                    logger.debug(
                        "分散ロック解放",
                        resource_id=resource_id,
                        lock_key=lock_key,
                    )
                    return True
                else:
                    logger.warning(
                        "ロック解放失敗（トークン不一致または期限切れ）",
                        resource_id=resource_id,
                        lock_key=lock_key,
                    )
                    return False

            except RedisError as e:
                logger.error(
                    "ロック解放時Redisエラー",
                    resource_id=resource_id,
                    error=str(e),
                )
                return False

    async def extend(
        self,
        resource_id: str,
        token: str,
        additional_ttl: int = DEFAULT_LOCK_TTL,
    ) -> bool:
        """
        ロックの有効期限を延長

        Args:
            resource_id: リソースID
            token: ロックトークン
            additional_ttl: 追加する有効期限（秒）

        Returns:
            延長成功かどうか
        """
        lock_key = self._make_lock_key(resource_id)
        ttl_ms = additional_ttl * 1000

        async with redis_client() as redis:
            try:
                result = await redis.eval(
                    self.EXTEND_SCRIPT,
                    1,
                    lock_key,
                    token,
                    ttl_ms,
                )

                if result == 1:
                    logger.debug(
                        "ロック延長",
                        resource_id=resource_id,
                        additional_ttl=additional_ttl,
                    )
                    return True
                else:
                    logger.warning(
                        "ロック延長失敗（トークン不一致または期限切れ）",
                        resource_id=resource_id,
                    )
                    return False

            except RedisError as e:
                logger.error(
                    "ロック延長時Redisエラー",
                    resource_id=resource_id,
                    error=str(e),
                )
                return False

    async def is_locked(self, resource_id: str) -> bool:
        """
        リソースがロックされているか確認

        Args:
            resource_id: リソースID

        Returns:
            ロックされているかどうか
        """
        lock_key = self._make_lock_key(resource_id)

        async with redis_client() as redis:
            try:
                return await redis.exists(lock_key) == 1
            except RedisError:
                return False

    @asynccontextmanager
    async def lock(
        self,
        resource_id: str,
        ttl: int = DEFAULT_LOCK_TTL,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
    ):
        """
        分散ロックのコンテキストマネージャー

        Args:
            resource_id: リソースID
            ttl: ロックの有効期限（秒）
            acquire_timeout: ロック取得の最大待機時間（秒）

        Yields:
            ロックトークン

        Raises:
            LockAcquisitionError: ロック取得に失敗した場合
        """
        token = await self.acquire(resource_id, ttl, acquire_timeout)
        try:
            yield token
        finally:
            await self.release(resource_id, token)


# 会話ロック専用マネージャー（シングルトン）
_conversation_lock_manager: Optional[DistributedLockManager] = None


def get_conversation_lock_manager() -> DistributedLockManager:
    """
    会話ロックマネージャーを取得

    Returns:
        DistributedLockManager インスタンス
    """
    global _conversation_lock_manager
    if _conversation_lock_manager is None:
        _conversation_lock_manager = DistributedLockManager(key_prefix="conversation:")
    return _conversation_lock_manager
