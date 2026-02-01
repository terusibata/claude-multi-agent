"""
グレースフルシャットダウン管理

シグナルハンドリングと進行中タスクの適切な終了を管理
"""
import asyncio
import signal
from contextlib import asynccontextmanager
from typing import Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


class ShutdownManager:
    """
    グレースフルシャットダウンを管理するクラス

    - SIGTERM/SIGINT シグナルをハンドリング
    - 進行中のタスクを追跡し、シャットダウン時に完了を待機
    - タイムアウト後は強制終了
    """

    def __init__(self, shutdown_timeout: float = 30.0):
        """
        初期化

        Args:
            shutdown_timeout: シャットダウン待機のタイムアウト秒数
        """
        self.shutdown_timeout = shutdown_timeout
        self._shutdown_event = asyncio.Event()
        self._active_tasks: set[asyncio.Task] = set()
        self._is_shutting_down = False
        self._cleanup_callbacks: list[Callable] = []

    @property
    def is_shutting_down(self) -> bool:
        """シャットダウン中かどうか"""
        return self._is_shutting_down

    def register_cleanup(self, callback: Callable) -> None:
        """
        シャットダウン時に実行するクリーンアップコールバックを登録

        Args:
            callback: 非同期クリーンアップ関数
        """
        self._cleanup_callbacks.append(callback)

    def track_task(self, task: asyncio.Task) -> None:
        """
        タスクを追跡対象に追加

        Args:
            task: 追跡するasyncioタスク
        """
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    def setup_signal_handlers(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """
        シグナルハンドラーを設定

        Args:
            loop: イベントループ（Noneの場合は現在のループを使用）
        """
        loop = loop or asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._handle_signal(s))
                )
                logger.info("シグナルハンドラー登録", signal=sig.name)
            except NotImplementedError:
                # Windows では add_signal_handler がサポートされない
                logger.warning(
                    "シグナルハンドラー登録スキップ（未サポート）",
                    signal=sig.name
                )

    async def _handle_signal(self, sig: signal.Signals) -> None:
        """
        シグナルを処理

        Args:
            sig: 受信したシグナル
        """
        if self._is_shutting_down:
            logger.warning("シャットダウン中に再度シグナル受信", signal=sig.name)
            return

        logger.info("シャットダウンシグナル受信", signal=sig.name)
        self._is_shutting_down = True
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """シャットダウンシグナルを待機"""
        await self._shutdown_event.wait()

    async def graceful_shutdown(self) -> None:
        """
        グレースフルシャットダウンを実行

        1. 新規リクエストの受付を停止（呼び出し側で制御）
        2. 進行中のタスクの完了を待機
        3. クリーンアップコールバックを実行
        """
        logger.info(
            "グレースフルシャットダウン開始",
            active_tasks=len(self._active_tasks),
            timeout=self.shutdown_timeout
        )

        # 進行中のタスクの完了を待機
        if self._active_tasks:
            logger.info("進行中タスクの完了を待機中", count=len(self._active_tasks))

            try:
                # タスクにキャンセルを通知
                for task in self._active_tasks:
                    task.cancel()

                # 完了を待機（タイムアウト付き）
                done, pending = await asyncio.wait(
                    self._active_tasks,
                    timeout=self.shutdown_timeout,
                    return_when=asyncio.ALL_COMPLETED
                )

                if pending:
                    logger.warning(
                        "タイムアウトにより一部タスクを強制終了",
                        pending_count=len(pending)
                    )
                    for task in pending:
                        task.cancel()
                        try:
                            await asyncio.wait_for(task, timeout=1.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                else:
                    logger.info("全タスクが正常に完了")

            except Exception as e:
                logger.error("タスク待機中にエラー", error=str(e))

        # クリーンアップコールバックを実行
        for callback in self._cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error("クリーンアップエラー", error=str(e))

        logger.info("グレースフルシャットダウン完了")


# グローバルインスタンス
_shutdown_manager: Optional[ShutdownManager] = None


def get_shutdown_manager(shutdown_timeout: Optional[float] = None) -> ShutdownManager:
    """
    シャットダウンマネージャーのシングルトンインスタンスを取得

    Args:
        shutdown_timeout: シャットダウンタイムアウト（初回のみ有効）
    """
    global _shutdown_manager
    if _shutdown_manager is None:
        from app.config import get_settings
        settings = get_settings()
        timeout = shutdown_timeout or settings.shutdown_timeout
        _shutdown_manager = ShutdownManager(shutdown_timeout=timeout)
    return _shutdown_manager


@asynccontextmanager
async def track_request():
    """
    リクエスト処理を追跡するコンテキストマネージャー

    使用例:
        async with track_request():
            # リクエスト処理
            pass
    """
    manager = get_shutdown_manager()

    if manager.is_shutting_down:
        raise RuntimeError("サーバーはシャットダウン中です")

    task = asyncio.current_task()
    if task:
        manager.track_task(task)

    try:
        yield
    finally:
        pass  # タスクは done_callback で自動的に削除される
