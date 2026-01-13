"""
セッションロック機構

同一セッションへの同時実行を防ぐためのロックマネージャー
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class SessionLockError(Exception):
    """セッションロック取得エラー"""

    def __init__(self, session_id: str, message: str = "セッションは現在使用中です"):
        self.session_id = session_id
        self.message = message
        super().__init__(f"{message}: {session_id}")


class SessionLockManager:
    """
    セッションロックマネージャー

    インメモリでセッションごとのロックを管理。
    将来的にRedisなどの分散ロックに置き換え可能な設計。
    """

    # ロックのデフォルトタイムアウト（秒）
    DEFAULT_LOCK_TIMEOUT = 600  # 10分
    # ロック取得の待機タイムアウト（秒）
    DEFAULT_ACQUIRE_TIMEOUT = 5

    def __init__(self):
        """初期化"""
        # セッションID -> (Lock, 取得時刻, タイムアウト)
        self._locks: dict[str, tuple[asyncio.Lock, datetime, int]] = {}
        # メンテナンス用のロック
        self._manager_lock = asyncio.Lock()

    async def acquire(
        self,
        session_id: str,
        timeout: int = DEFAULT_LOCK_TIMEOUT,
        wait_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
    ) -> bool:
        """
        セッションロックを取得

        Args:
            session_id: セッションID
            timeout: ロックのタイムアウト（秒）
            wait_timeout: ロック取得の待機タイムアウト（秒）

        Returns:
            取得成功かどうか

        Raises:
            SessionLockError: ロック取得に失敗した場合
        """
        async with self._manager_lock:
            # 既存のロックがあるか確認
            if session_id in self._locks:
                lock, acquired_at, lock_timeout = self._locks[session_id]

                # ロックがタイムアウトしているか確認
                if datetime.utcnow() - acquired_at > timedelta(seconds=lock_timeout):
                    # タイムアウトしたロックを解放
                    logger.warning(
                        "タイムアウトしたセッションロックを強制解放",
                        session_id=session_id,
                        acquired_at=acquired_at.isoformat(),
                    )
                    del self._locks[session_id]
                elif lock.locked():
                    # 他の処理がロックを保持中
                    raise SessionLockError(
                        session_id,
                        "セッションは現在別の処理で使用中です"
                    )

            # 新しいロックを作成
            lock = asyncio.Lock()
            self._locks[session_id] = (lock, datetime.utcnow(), timeout)

        # ロックを取得
        try:
            acquired = await asyncio.wait_for(
                lock.acquire(),
                timeout=wait_timeout,
            )
            if acquired:
                logger.debug(
                    "セッションロック取得",
                    session_id=session_id,
                    timeout=timeout,
                )
            return acquired
        except asyncio.TimeoutError:
            # 取得タイムアウト
            async with self._manager_lock:
                if session_id in self._locks:
                    del self._locks[session_id]
            raise SessionLockError(
                session_id,
                f"ロック取得がタイムアウトしました（{wait_timeout}秒）"
            )

    async def release(self, session_id: str) -> bool:
        """
        セッションロックを解放

        Args:
            session_id: セッションID

        Returns:
            解放成功かどうか
        """
        async with self._manager_lock:
            if session_id not in self._locks:
                logger.warning(
                    "存在しないセッションロックの解放を試行",
                    session_id=session_id,
                )
                return False

            lock, _, _ = self._locks[session_id]

            if lock.locked():
                lock.release()

            del self._locks[session_id]

            logger.debug(
                "セッションロック解放",
                session_id=session_id,
            )
            return True

    def is_locked(self, session_id: str) -> bool:
        """
        セッションがロックされているか確認

        Args:
            session_id: セッションID

        Returns:
            ロックされているかどうか
        """
        if session_id not in self._locks:
            return False

        lock, acquired_at, timeout = self._locks[session_id]

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

            for session_id, (lock, acquired_at, timeout) in self._locks.items():
                if now - acquired_at > timedelta(seconds=timeout):
                    expired.append(session_id)

            for session_id in expired:
                if self._locks[session_id][0].locked():
                    self._locks[session_id][0].release()
                del self._locks[session_id]

            if expired:
                logger.info(
                    "期限切れセッションロックをクリーンアップ",
                    count=len(expired),
                )

            return len(expired)

    @asynccontextmanager
    async def lock(
        self,
        session_id: str,
        timeout: int = DEFAULT_LOCK_TIMEOUT,
        wait_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
    ):
        """
        セッションロックのコンテキストマネージャー

        Args:
            session_id: セッションID
            timeout: ロックのタイムアウト（秒）
            wait_timeout: ロック取得の待機タイムアウト（秒）

        Yields:
            None

        Raises:
            SessionLockError: ロック取得に失敗した場合
        """
        await self.acquire(session_id, timeout, wait_timeout)
        try:
            yield
        finally:
            await self.release(session_id)


# グローバルインスタンス（シングルトン）
_session_lock_manager: Optional[SessionLockManager] = None


def get_session_lock_manager() -> SessionLockManager:
    """
    セッションロックマネージャーを取得

    Returns:
        SessionLockManager インスタンス
    """
    global _session_lock_manager
    if _session_lock_manager is None:
        _session_lock_manager = SessionLockManager()
    return _session_lock_manager
