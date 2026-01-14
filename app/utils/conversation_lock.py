"""
会話ロック機構

同一会話への同時実行を防ぐためのロックマネージャー
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class ConversationLockError(Exception):
    """会話ロック取得エラー"""

    def __init__(self, conversation_id: str, message: str = "会話は現在使用中です"):
        self.conversation_id = conversation_id
        self.message = message
        super().__init__(f"{message}: {conversation_id}")


class ConversationLockManager:
    """
    会話ロックマネージャー

    インメモリで会話ごとのロックを管理。
    将来的にRedisなどの分散ロックに置き換え可能な設計。
    """

    # ロックのデフォルトタイムアウト（秒）
    DEFAULT_LOCK_TIMEOUT = 600  # 10分
    # ロック取得の待機タイムアウト（秒）
    DEFAULT_ACQUIRE_TIMEOUT = 5

    def __init__(self):
        """初期化"""
        # 会話ID -> (Lock, 取得時刻, タイムアウト)
        self._locks: dict[str, tuple[asyncio.Lock, datetime, int]] = {}
        # メンテナンス用のロック
        self._manager_lock = asyncio.Lock()

    async def acquire(
        self,
        conversation_id: str,
        timeout: int = DEFAULT_LOCK_TIMEOUT,
        wait_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
    ) -> bool:
        """
        会話ロックを取得

        Args:
            conversation_id: 会話ID
            timeout: ロックのタイムアウト（秒）
            wait_timeout: ロック取得の待機タイムアウト（秒）

        Returns:
            取得成功かどうか

        Raises:
            ConversationLockError: ロック取得に失敗した場合
        """
        async with self._manager_lock:
            # 既存のロックがあるか確認
            if conversation_id in self._locks:
                lock, acquired_at, lock_timeout = self._locks[conversation_id]

                # ロックがタイムアウトしているか確認
                if datetime.utcnow() - acquired_at > timedelta(seconds=lock_timeout):
                    # タイムアウトしたロックを解放
                    logger.warning(
                        "タイムアウトした会話ロックを強制解放",
                        conversation_id=conversation_id,
                        acquired_at=acquired_at.isoformat(),
                    )
                    del self._locks[conversation_id]
                elif lock.locked():
                    # 他の処理がロックを保持中
                    raise ConversationLockError(
                        conversation_id,
                        "会話は現在別の処理で使用中です"
                    )

            # 新しいロックを作成
            lock = asyncio.Lock()
            self._locks[conversation_id] = (lock, datetime.utcnow(), timeout)

        # ロックを取得
        try:
            acquired = await asyncio.wait_for(
                lock.acquire(),
                timeout=wait_timeout,
            )
            if acquired:
                logger.debug(
                    "会話ロック取得",
                    conversation_id=conversation_id,
                    timeout=timeout,
                )
            return acquired
        except asyncio.TimeoutError:
            # 取得タイムアウト
            async with self._manager_lock:
                if conversation_id in self._locks:
                    del self._locks[conversation_id]
            raise ConversationLockError(
                conversation_id,
                f"ロック取得がタイムアウトしました（{wait_timeout}秒）"
            )

    async def release(self, conversation_id: str) -> bool:
        """
        会話ロックを解放

        Args:
            conversation_id: 会話ID

        Returns:
            解放成功かどうか
        """
        async with self._manager_lock:
            if conversation_id not in self._locks:
                logger.warning(
                    "存在しない会話ロックの解放を試行",
                    conversation_id=conversation_id,
                )
                return False

            lock, _, _ = self._locks[conversation_id]

            if lock.locked():
                lock.release()

            del self._locks[conversation_id]

            logger.debug(
                "会話ロック解放",
                conversation_id=conversation_id,
            )
            return True

    def is_locked(self, conversation_id: str) -> bool:
        """
        会話がロックされているか確認

        Args:
            conversation_id: 会話ID

        Returns:
            ロックされているかどうか
        """
        if conversation_id not in self._locks:
            return False

        lock, acquired_at, timeout = self._locks[conversation_id]

        # タイムアウトチェック
        if datetime.utcnow() - acquired_at > timedelta(seconds=timeout):
            return False

        return lock.locked()

    async def cleanup_expired(self) -> int:
        """
        期限切れのロックをクリーンアップ

        Returns:
            クリーンアップしたロック数
        """
        async with self._manager_lock:
            expired = []
            now = datetime.utcnow()

            for conversation_id, (lock, acquired_at, timeout) in self._locks.items():
                if now - acquired_at > timedelta(seconds=timeout):
                    expired.append(conversation_id)

            for conversation_id in expired:
                if self._locks[conversation_id][0].locked():
                    self._locks[conversation_id][0].release()
                del self._locks[conversation_id]

            if expired:
                logger.info(
                    "期限切れ会話ロックをクリーンアップ",
                    count=len(expired),
                )

            return len(expired)

    @asynccontextmanager
    async def lock(
        self,
        conversation_id: str,
        timeout: int = DEFAULT_LOCK_TIMEOUT,
        wait_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
    ):
        """
        会話ロックのコンテキストマネージャー

        Args:
            conversation_id: 会話ID
            timeout: ロックのタイムアウト（秒）
            wait_timeout: ロック取得の待機タイムアウト（秒）

        Yields:
            None

        Raises:
            ConversationLockError: ロック取得に失敗した場合
        """
        await self.acquire(conversation_id, timeout, wait_timeout)
        try:
            yield
        finally:
            await self.release(conversation_id)


# グローバルインスタンス（シングルトン）
_conversation_lock_manager: Optional[ConversationLockManager] = None


def get_conversation_lock_manager() -> ConversationLockManager:
    """
    会話ロックマネージャーを取得

    Returns:
        ConversationLockManager インスタンス
    """
    global _conversation_lock_manager
    if _conversation_lock_manager is None:
        _conversation_lock_manager = ConversationLockManager()
    return _conversation_lock_manager
