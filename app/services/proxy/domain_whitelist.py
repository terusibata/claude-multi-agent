"""
ドメインホワイトリスト
許可されたドメインのみ外部通信を許可する
"""
from urllib.parse import urlparse


class DomainWhitelist:
    """ドメインベースのアクセス制御"""

    def __init__(self, allowed_domains: list[str]) -> None:
        self._allowed = set(d.strip().lower() for d in allowed_domains if d.strip())

    def is_allowed(self, url: str) -> bool:
        """URLのドメインがホワイトリストに含まれるか判定"""
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except Exception:
            return False

        if not host:
            return False

        for allowed in self._allowed:
            if host == allowed or host.endswith(f".{allowed}"):
                return True

        return False

    @property
    def domains(self) -> frozenset[str]:
        """許可ドメイン一覧"""
        return frozenset(self._allowed)
