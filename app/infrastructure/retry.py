"""
リトライユーティリティ

外部サービス呼び出しのリトライロジックを提供
"""
import asyncio
import random
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Optional, Type, TypeVar, Union

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar('T')


@dataclass
class RetryConfig:
    """リトライ設定"""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True
    # 呼び出し元が具体的な例外を指定すること（Exceptionの使用は非推奨）
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,)


def calculate_delay(
    attempt: int,
    config: RetryConfig,
) -> float:
    """
    リトライ遅延時間を計算（Exponential Backoff with Jitter）

    Args:
        attempt: 現在の試行回数（0始まり）
        config: リトライ設定

    Returns:
        遅延時間（秒）
    """
    delay = min(
        config.base_delay * (config.exponential_base ** attempt),
        config.max_delay
    )

    if config.jitter:
        # Full Jitter: [0, delay] の範囲でランダム化
        delay = random.uniform(0, delay)

    return delay


async def retry_async(
    func: Callable[..., T],
    *args,
    config: Optional[RetryConfig] = None,
    operation_name: str = "operation",
    **kwargs,
) -> T:
    """
    非同期関数をリトライ付きで実行

    Args:
        func: 実行する非同期関数
        *args: 関数の引数
        config: リトライ設定
        operation_name: ログ用の操作名
        **kwargs: 関数のキーワード引数

    Returns:
        関数の戻り値

    Raises:
        Exception: 全リトライ失敗時は最後の例外を再送出
    """
    config = config or RetryConfig()
    last_exception: Optional[Exception] = None

    for attempt in range(config.max_attempts):
        try:
            return await func(*args, **kwargs)

        except config.retryable_exceptions as e:
            last_exception = e
            is_last_attempt = attempt == config.max_attempts - 1

            if is_last_attempt:
                logger.error(
                    f"{operation_name} 失敗（リトライ上限）",
                    attempt=attempt + 1,
                    max_attempts=config.max_attempts,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                raise

            delay = calculate_delay(attempt, config)
            logger.warning(
                f"{operation_name} リトライ",
                attempt=attempt + 1,
                max_attempts=config.max_attempts,
                delay=delay,
                error=str(e),
                error_type=type(e).__name__,
            )
            await asyncio.sleep(delay)

    # 通常ここには到達しないが、型チェックのため
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected retry state")


def retry_sync(
    func: Callable[..., T],
    *args,
    config: Optional[RetryConfig] = None,
    operation_name: str = "operation",
    **kwargs,
) -> T:
    """
    同期関数をリトライ付きで実行

    Args:
        func: 実行する同期関数
        *args: 関数の引数
        config: リトライ設定
        operation_name: ログ用の操作名
        **kwargs: 関数のキーワード引数

    Returns:
        関数の戻り値

    Raises:
        Exception: 全リトライ失敗時は最後の例外を再送出
    """
    import time

    config = config or RetryConfig()
    last_exception: Optional[Exception] = None

    for attempt in range(config.max_attempts):
        try:
            return func(*args, **kwargs)

        except config.retryable_exceptions as e:
            last_exception = e
            is_last_attempt = attempt == config.max_attempts - 1

            if is_last_attempt:
                logger.error(
                    f"{operation_name} 失敗（リトライ上限）",
                    attempt=attempt + 1,
                    max_attempts=config.max_attempts,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                raise

            delay = calculate_delay(attempt, config)
            logger.warning(
                f"{operation_name} リトライ",
                attempt=attempt + 1,
                max_attempts=config.max_attempts,
                delay=delay,
                error=str(e),
                error_type=type(e).__name__,
            )
            time.sleep(delay)

    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected retry state")


def with_retry(
    config: Optional[RetryConfig] = None,
    operation_name: Optional[str] = None,
):
    """
    リトライ付き実行のデコレーター

    Args:
        config: リトライ設定
        operation_name: ログ用の操作名

    使用例:
        @with_retry(RetryConfig(max_attempts=3))
        async def call_external_api():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        name = operation_name or func.__name__

        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs) -> T:
                return await retry_async(
                    func, *args,
                    config=config,
                    operation_name=name,
                    **kwargs
                )
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs) -> T:
                return retry_sync(
                    func, *args,
                    config=config,
                    operation_name=name,
                    **kwargs
                )
            return sync_wrapper

    return decorator
