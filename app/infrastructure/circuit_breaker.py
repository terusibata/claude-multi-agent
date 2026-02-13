"""
サーキットブレーカー

外部サービス（Bedrock等）の障害時にfast-failを実現し、
不必要なリトライによるリソース浪費を防止する。
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Callable, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar('T')


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """サーキットブレーカーがオープン状態の場合のエラー"""

    def __init__(self, name: str, reset_timeout: float):
        self.circuit_name = name
        self.reset_timeout = reset_timeout
        super().__init__(f"サーキットブレーカー '{name}' はオープン状態です")


@dataclass
class CircuitBreakerConfig:
    """サーキットブレーカー設定"""
    failure_threshold: int = 5
    reset_timeout: float = 60.0
    half_open_max_calls: int = 1


@dataclass
class CircuitBreaker:
    """
    サーキットブレーカー実装

    状態遷移:
        CLOSED -> (failure_threshold回失敗) -> OPEN
        OPEN -> (reset_timeout経過) -> HALF_OPEN
        HALF_OPEN -> (成功) -> CLOSED
        HALF_OPEN -> (失敗) -> OPEN
    """
    name: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time > self.config.reset_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info(
                        "サーキットブレーカー half-open",
                        circuit=self.name,
                    )
            return self._state

    def execute(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """
        サーキットブレーカーを通して関数を実行

        Args:
            fn: 実行する関数
            *args: 関数の引数
            **kwargs: 関数のキーワード引数

        Returns:
            関数の戻り値

        Raises:
            CircuitOpenError: サーキットがオープン状態の場合
        """
        current_state = self.state
        if current_state == CircuitState.OPEN:
            logger.warning(
                "サーキットブレーカー オープン状態、fast-fail",
                circuit=self.name,
                failure_count=self._failure_count,
            )
            raise CircuitOpenError(self.name, self.config.reset_timeout)

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info(
                    "サーキットブレーカー クローズ",
                    circuit=self.name,
                )
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def _on_failure(self, error: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                logger.error(
                    "サーキットブレーカー オープン",
                    circuit=self.name,
                    failure_count=self._failure_count,
                    error=str(error),
                    error_type=type(error).__name__,
                )
