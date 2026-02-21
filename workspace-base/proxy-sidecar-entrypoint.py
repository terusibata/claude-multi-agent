"""
ECSサイドカー Proxy + Admin HTTPサーバー起動スクリプト

タスク定義内でworkspace-agentコンテナと並行して起動される。
- TCP :8080 → Credential Injection Proxy（workspace-agentからHTTP_PROXY経由で利用）
- TCP :8081 → Admin HTTP（Backendからのルール更新用）
"""
import asyncio
import os
import signal
import sys

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger("proxy-sidecar")


async def main() -> None:
    from app.config import get_settings
    from app.services.proxy.credential_proxy import (
        CredentialInjectionProxy,
        ProxyAdminServer,
        ProxyConfig,
    )
    from app.services.proxy.sigv4 import AWSCredentials

    settings = get_settings()
    proxy_port = int(os.environ.get("PROXY_PORT", "8080"))
    admin_port = int(os.environ.get("PROXY_ADMIN_PORT", "8081"))

    aws_creds = AWSCredentials(
        access_key_id=settings.aws_access_key_id or "",
        secret_access_key=settings.aws_secret_access_key or "",
        session_token=settings.aws_session_token,
        region=settings.aws_region,
    )
    proxy_config = ProxyConfig(
        whitelist_domains=settings.proxy_domain_whitelist_list,
        aws_credentials=aws_creds,
        log_all_requests=settings.proxy_log_all_requests,
    )

    # ECSサイドカーではTCPリスンが必要
    # CredentialInjectionProxy は UDS だが、サイドカーでは同一タスク内の
    # localhost:8080 で workspace-agent から接続されるため、
    # socat 等で TCP→UDS 変換するか、直接 TCP リスンに変更する。
    # ここでは asyncio.start_server で TCP リスンし、Proxy ロジックを再利用する。

    # UDS の代わりに TCP で直接起動するためにパッチ
    socket_path = "/tmp/proxy-internal.sock"
    proxy = CredentialInjectionProxy(proxy_config, socket_path)

    # TCP リスン版の start
    proxy._http_client = __import__("httpx").AsyncClient(
        timeout=__import__("httpx").Timeout(60.0, connect=10.0),
        limits=__import__("httpx").Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30.0,
        ),
    )
    proxy._server = await asyncio.start_server(
        proxy._handle_connection,
        host="0.0.0.0",
        port=proxy_port,
    )
    logger.info("Proxy TCP起動", port=proxy_port)

    # Admin HTTP サーバー起動
    admin = ProxyAdminServer(proxy, port=admin_port)
    await admin.start()

    # シグナルハンドラ
    stop_event = asyncio.Event()

    def _signal_handler():
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Proxyサイドカー起動完了", proxy_port=proxy_port, admin_port=admin_port)
    await stop_event.wait()

    # クリーンアップ
    await admin.stop()
    if proxy._server:
        proxy._server.close()
        await proxy._server.wait_closed()
    if proxy._http_client:
        await proxy._http_client.aclose()
    logger.info("Proxyサイドカー停止完了")


if __name__ == "__main__":
    asyncio.run(main())
