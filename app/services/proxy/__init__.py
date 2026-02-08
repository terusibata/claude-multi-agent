"""
Credential Injection Proxy
コンテナからの外部通信をUnix Socket経由で中継し、
ドメインホワイトリストとAWS認証情報注入を行う
"""
from app.services.proxy.credential_proxy import CredentialInjectionProxy
from app.services.proxy.domain_whitelist import DomainWhitelist

__all__ = [
    "CredentialInjectionProxy",
    "DomainWhitelist",
]
