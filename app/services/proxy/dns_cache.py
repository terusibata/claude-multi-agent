"""
TTL付きDNSキャッシュ
ホワイトリスト対象ドメインのDNS解決結果をキャッシュし、レイテンシを低減する
"""
import asyncio
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TTL_SECONDS = 300  # 5 minutes


@dataclass(frozen=True)
class _CacheEntry:
    """キャッシュエントリ"""

    addresses: list[str]
    expires_at: float


class DNSCache:
    """
    非同期・スレッドセーフなTTL付きDNSキャッシュ

    ホワイトリスト対象ドメインのDNS解決結果をメモリにキャッシュし、
    繰り返しの名前解決によるレイテンシを削減する。
    エントリはTTL経過後に自動的に無効化される。
    """

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def resolve(self, hostname: str) -> list[str]:
        """
        ホスト名をIPアドレスのリストに解決する

        キャッシュに有効なエントリがあればそれを返し、
        なければDNS解決を行い結果をキャッシュする。

        Args:
            hostname: 解決対象のホスト名

        Returns:
            IPアドレスの文字列リスト
        """
        now = time.monotonic()

        async with self._lock:
            entry = self._cache.get(hostname)
            if entry is not None and entry.expires_at > now:
                logger.debug("DNSキャッシュヒット", hostname=hostname)
                return list(entry.addresses)

        # ロック外でDNS解決を実行（ブロッキング回避）
        addresses = await self._do_resolve(hostname)

        async with self._lock:
            self._cache[hostname] = _CacheEntry(
                addresses=addresses,
                expires_at=time.monotonic() + self._ttl,
            )

        logger.debug(
            "DNS解決完了・キャッシュ保存",
            hostname=hostname,
            addresses=addresses,
            ttl_seconds=self._ttl,
        )
        return addresses

    async def clear(self) -> None:
        """キャッシュを全消去する"""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
        logger.info("DNSキャッシュクリア", entries_removed=count)

    async def _do_resolve(self, hostname: str) -> list[str]:
        """asyncio経由でDNS解決を実行"""
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(hostname, None)
        except OSError as e:
            logger.error("DNS解決失敗", hostname=hostname, error=str(e))
            raise

        # 重複を除いたIPアドレスリストを返す（順序保持）
        seen: set[str] = set()
        addresses: list[str] = []
        for family, _type, _proto, _canonname, sockaddr in infos:
            addr = sockaddr[0]
            if addr not in seen:
                seen.add(addr)
                addresses.append(addr)

        return addresses
