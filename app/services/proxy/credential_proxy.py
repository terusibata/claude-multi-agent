"""
Credential Injection Proxy
Unix Socket上で動作し、コンテナからの全外部通信を中継する

- ドメインホワイトリストによるアクセス制御
- Bedrock API向けSigV4認証情報の自動注入
- 全リクエストの監査ログ出力
"""
import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import structlog

from app.infrastructure.metrics import (
    get_workspace_proxy_blocked,
    get_workspace_proxy_request_duration,
)
from app.services.proxy.dns_cache import DNSCache
from app.services.proxy.domain_whitelist import DomainWhitelist
from app.services.proxy.sigv4 import AWSCredentials, sign_request

logger = structlog.get_logger(__name__)


@dataclass
class ProxyConfig:
    """Proxy設定"""

    whitelist_domains: list[str]
    aws_credentials: AWSCredentials
    log_all_requests: bool = True


class CredentialInjectionProxy:
    """
    Unix Socket上で動作するHTTP Forward Proxy

    コンテナ内のHTTP_PROXY/HTTPS_PROXYがこのソケットを指す。
    - 許可ドメインのみ通信許可
    - bedrock-runtime ドメインにはSigV4認証を自動注入
    - 全リクエストを監査ログに記録
    """

    def __init__(self, config: ProxyConfig, socket_path: str) -> None:
        self.config = config
        self.socket_path = socket_path
        self._whitelist = DomainWhitelist(config.whitelist_domains)
        self._dns_cache = DNSCache(ttl_seconds=300)
        self._http_client: httpx.AsyncClient | None = None
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Proxyサーバーを起動"""
        socket_dir = Path(self.socket_path).parent
        socket_dir.mkdir(parents=True, exist_ok=True)

        # 既存ソケットファイルを削除
        socket_file = Path(self.socket_path)
        if socket_file.exists():
            socket_file.unlink()

        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
        )
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self.socket_path,
        )
        logger.info("Proxy起動", socket_path=self.socket_path)

    async def stop(self) -> None:
        """Proxyサーバーを停止"""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._http_client:
            await self._http_client.aclose()
        logger.info("Proxy停止", socket_path=self.socket_path)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """クライアント接続をハンドル"""
        try:
            request_line = await reader.readline()
            if not request_line:
                return

            request_str = request_line.decode("utf-8", errors="replace").strip()
            parts = request_str.split(" ")
            if len(parts) < 3:
                writer.close()
                return

            method = parts[0]
            url = parts[1]

            # ヘッダー読み取り
            headers: dict[str, str] = {}
            content_length = 0
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                header_str = line.decode("utf-8", errors="replace").strip()
                if ":" in header_str:
                    key, value = header_str.split(":", 1)
                    headers[key.strip()] = value.strip()
                    if key.strip().lower() == "content-length":
                        content_length = int(value.strip())

            # ボディ読み取り
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            # CONNECT メソッド（TLSパススルー）
            # CONNECTはトンネル確立後に双方向パイプで通信するため、
            # _handle_connect内でレスポンス送信とwriter closeが完了する。
            # したがってCONNECT後はearly returnする（BUG-04修正）。
            if method == "CONNECT":
                await self._handle_connect(url, reader, writer)
                return

            status, resp_headers, resp_body = await self.handle_request(
                method, url, headers, body
            )

            # レスポンス送信（HTTP平文リクエストのみ）
            response_line = f"HTTP/1.1 {status} {'OK' if status < 400 else 'Error'}\r\n"
            writer.write(response_line.encode())
            for k, v in resp_headers.items():
                writer.write(f"{k}: {v}\r\n".encode())
            writer.write(f"Content-Length: {len(resp_body)}\r\n".encode())
            writer.write(b"\r\n")
            writer.write(resp_body)
            await writer.drain()

        except Exception as e:
            logger.error("Proxy接続エラー", error=str(e))
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def handle_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        """
        HTTPリクエストを処理

        Returns:
            (ステータスコード, レスポンスヘッダー, レスポンスボディ)
        """
        request_start = time.perf_counter()

        # ドメインチェック
        if not self._whitelist.is_allowed(url):
            get_workspace_proxy_blocked().inc()
            if self.config.log_all_requests:
                logger.warning("Proxy: ドメイン拒否", method=method, url=url)
            return 403, {}, b"Domain not in whitelist"

        # Bedrock APIへのリクエストにSigV4認証情報を注入
        if "bedrock-runtime" in url:
            headers = sign_request(
                credentials=self.config.aws_credentials,
                method=method,
                url=url,
                headers=headers,
                body=body,
                service="bedrock",
            )

        if self.config.log_all_requests:
            logger.info("Proxy: 転送", method=method, url=url)

        result = await self._forward_request(method, url, headers, body)

        # レイテンシメトリクス
        duration = time.perf_counter() - request_start
        get_workspace_proxy_request_duration().observe(duration, method=method)
        if self.config.log_all_requests:
            logger.info("Proxy: 完了", method=method, url=url, duration_ms=round(duration * 1000, 1), status=result[0])

        return result

    async def _handle_connect(
        self,
        host_port: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        CONNECT メソッド（TLSパススルー）

        レスポンス送信・writerクローズまで全てこのメソッド内で完結する。
        """
        # ホスト名を検証
        host = host_port.split(":")[0]
        dummy_url = f"https://{host}/"

        if not self._whitelist.is_allowed(dummy_url):
            get_workspace_proxy_blocked().inc()
            logger.warning("Proxy: CONNECT拒否", host=host_port)
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 26\r\n\r\nDomain not in whitelist\r\n")
            await writer.drain()
            return  # writer は caller (_handle_connection) の finally でクローズ

        logger.info("Proxy: CONNECT", host=host_port)
        # 200 Connection Established を返してTLS tunnel を確立
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        # 双方向プロキシ
        try:
            target_host, target_port_str = host_port.split(":")
            target_port = int(target_port_str)
        except ValueError:
            target_host = host_port
            target_port = 443

        remote_reader = None
        remote_writer = None
        try:
            # DNSキャッシュ経由でホスト名を解決
            resolved_addrs = await self._dns_cache.resolve(target_host)
            connect_host = resolved_addrs[0] if resolved_addrs else target_host
            remote_reader, remote_writer = await asyncio.open_connection(
                connect_host, target_port
            )
        except Exception as e:
            logger.error("Proxy: CONNECT先接続失敗", host=host_port, error=str(e))
            return  # writer は caller (_handle_connection) の finally でクローズ

        async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass

        try:
            await asyncio.gather(
                _pipe(reader, remote_writer),
                _pipe(remote_reader, writer),
            )
        finally:
            # remote_writer のみクローズ（writer は caller がクローズ）
            if remote_writer:
                try:
                    if not remote_writer.is_closing():
                        remote_writer.close()
                except Exception:
                    pass

    async def _forward_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        """リクエストを外部に転送"""
        if not self._http_client:
            return 503, {}, b"Proxy not initialized"

        try:
            resp = await self._http_client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
            )
            resp_headers = dict(resp.headers)
            return resp.status_code, resp_headers, resp.content
        except httpx.TimeoutException:
            logger.error("Proxy: タイムアウト", method=method, url=url)
            return 504, {}, b"Gateway Timeout"
        except Exception as e:
            logger.error("Proxy: 転送エラー", method=method, url=url, error=str(e))
            return 502, {}, b"Bad Gateway"
