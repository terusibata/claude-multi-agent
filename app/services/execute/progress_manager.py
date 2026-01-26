"""
進捗管理サービス

処理の進捗状態を管理し、ユーザーフレンドリーなメッセージを生成
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Optional


def _utc_now() -> datetime:
    """タイムゾーン対応のUTC現在時刻を取得（Python 3.12+ の非推奨対応）"""
    return datetime.now(timezone.utc)

import structlog

from app.services.execute.progress_messages import get_initial_message, get_waiting_message
from app.services.execute.tool_labels import get_tool_label
from app.utils.streaming import format_progress_event

logger = structlog.get_logger(__name__)


@dataclass
class PhaseState:
    """フェーズの状態"""

    phase: str  # "thinking" | "generating" | "tool"
    tool_name: Optional[str] = None
    tool_use_id: Optional[str] = None
    tool_status: Optional[str] = None
    started_at: datetime = field(default_factory=_utc_now)
    last_message_at: datetime = field(default_factory=_utc_now)
    message_count: int = 0


class ProgressManager:
    """
    進捗管理マネージャー

    処理フェーズの状態を管理し、定期的に進捗メッセージを生成する
    """

    def __init__(
        self,
        seq_provider: Callable[[], int],
        interval_seconds: float = 3.0,
        parent_agent_id: Optional[str] = None,
    ):
        """
        Args:
            seq_provider: シーケンス番号を取得するコールバック
            interval_seconds: 進捗メッセージの送信間隔（秒）
            parent_agent_id: 親エージェントID（サブエージェントの場合）
        """
        self._seq_provider = seq_provider
        self._interval = interval_seconds
        self._parent_agent_id = parent_agent_id
        self._current_phase: Optional[PhaseState] = None
        self._is_running = False
        self._ticker_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._lock = asyncio.Lock()

    @property
    def is_active(self) -> bool:
        """フェーズがアクティブかどうか"""
        return self._current_phase is not None

    async def start_phase(
        self,
        phase: str,
        tool_name: Optional[str] = None,
        tool_use_id: Optional[str] = None,
        tool_status: Optional[str] = None,
        send_initial: bool = True,
    ) -> Optional[dict]:
        """
        新しいフェーズを開始

        Args:
            phase: フェーズ名（thinking, generating, tool）
            tool_name: ツール名（phaseがtoolの場合）
            tool_use_id: ツール使用ID
            tool_status: ツールステータス
            send_initial: 初期メッセージを返すか

        Returns:
            初期progressイベント（send_initial=Trueの場合）
        """
        async with self._lock:
            now = _utc_now()
            self._current_phase = PhaseState(
                phase=phase,
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                tool_status=tool_status,
                started_at=now,
                last_message_at=now,
            )

            if send_initial:
                message = get_initial_message(phase, tool_name)
                return self._create_progress_event(message)

            return None

    async def update_tool_status(self, tool_status: str) -> Optional[dict]:
        """
        ツールステータスを更新

        Args:
            tool_status: 新しいステータス

        Returns:
            progressイベント
        """
        async with self._lock:
            if self._current_phase and self._current_phase.phase == "tool":
                self._current_phase.tool_status = tool_status
                message = get_initial_message("tool", self._current_phase.tool_name)
                return self._create_progress_event(message)
            return None

    async def end_phase(self, final_message: Optional[str] = None) -> Optional[dict]:
        """
        現在のフェーズを終了

        Args:
            final_message: 終了時のメッセージ（省略時はイベントなし）

        Returns:
            最終progressイベント（final_messageが指定された場合）
        """
        async with self._lock:
            event = None
            if final_message and self._current_phase:
                event = self._create_progress_event(final_message)
            self._current_phase = None
            return event

    async def get_waiting_progress(self) -> Optional[dict]:
        """
        待機中の進捗メッセージを取得（3秒以上経過している場合）

        Returns:
            progressイベント（待機中の場合）、それ以外はNone
        """
        async with self._lock:
            if not self._current_phase:
                return None

            now = _utc_now()
            elapsed = (now - self._current_phase.last_message_at).total_seconds()

            if elapsed >= self._interval:
                phase = self._current_phase.phase
                tool_name = self._current_phase.tool_name
                tool_label = get_tool_label(tool_name) if tool_name else None

                message = get_waiting_message(phase, tool_name, tool_label)
                self._current_phase.last_message_at = now
                self._current_phase.message_count += 1

                return self._create_progress_event(message)

            return None

    def _create_progress_event(self, message: str) -> dict:
        """進捗イベントを作成"""
        phase = self._current_phase
        if not phase:
            return format_progress_event(
                seq=self._seq_provider(),
                progress_type="generating",
                message=message,
            )

        return format_progress_event(
            seq=self._seq_provider(),
            progress_type=phase.phase,
            message=message,
            tool_use_id=phase.tool_use_id,
            tool_name=phase.tool_name,
            tool_status=phase.tool_status,
            parent_agent_id=self._parent_agent_id,
        )

    async def start_ticker(self) -> None:
        """バックグラウンドのティッカーを開始"""
        if self._is_running:
            return

        self._is_running = True
        self._ticker_task = asyncio.create_task(self._ticker_loop())

    async def stop_ticker(self) -> None:
        """ティッカーを停止"""
        self._is_running = False
        if self._ticker_task:
            self._ticker_task.cancel()
            try:
                await self._ticker_task
            except asyncio.CancelledError:
                pass
            self._ticker_task = None

    async def _ticker_loop(self) -> None:
        """ティッカーのメインループ"""
        while self._is_running:
            try:
                await asyncio.sleep(self._interval)

                if not self._is_running:
                    break

                progress = await self.get_waiting_progress()
                if progress:
                    await self._message_queue.put(progress)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("ProgressManager ticker error", error=str(e))

    async def get_queued_messages(self) -> AsyncIterator[dict]:
        """
        キューに溜まったメッセージを取得

        Yields:
            progressイベント
        """
        while True:
            try:
                # 非ブロッキングで取得
                message = self._message_queue.get_nowait()
                yield message
            except asyncio.QueueEmpty:
                break

    def drain_queue(self) -> list[dict]:
        """
        キューのメッセージをすべて取得（同期版）

        Returns:
            progressイベントのリスト
        """
        messages = []
        while True:
            try:
                message = self._message_queue.get_nowait()
                messages.append(message)
            except asyncio.QueueEmpty:
                break
        return messages

    def set_tool_phase(
        self,
        phase: str,
        tool_name: Optional[str] = None,
        tool_use_id: Optional[str] = None,
        tool_status: Optional[str] = None,
    ) -> None:
        """
        ツールフェーズを直接設定（同期版）

        内部状態を設定するためのパブリックメソッド。
        asyncioロックを使用しないので、同期コンテキストから呼び出し可能。

        Args:
            phase: フェーズ名（thinking, generating, tool）
            tool_name: ツール名（phaseがtoolの場合）
            tool_use_id: ツール使用ID
            tool_status: ツールステータス
        """
        now = _utc_now()
        self._current_phase = PhaseState(
            phase=phase,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            tool_status=tool_status,
            started_at=now,
            last_message_at=now,
        )

    def clear_phase(self) -> None:
        """
        フェーズをクリア（同期版）

        内部状態をクリアするためのパブリックメソッド。
        """
        self._current_phase = None

    @property
    def current_phase(self) -> Optional[PhaseState]:
        """現在のフェーズ状態を取得（読み取り専用）"""
        return self._current_phase
