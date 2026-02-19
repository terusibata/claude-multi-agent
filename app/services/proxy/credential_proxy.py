"""
Credential Injection Proxy
Unix Socket上で動作し、コンテナからの全外部通信を中継する

- ドメインホワイトリストによるアクセス制御
- Bedrock API向けSigV4認証情報の自動注入
- MCP API向け認証ヘッダーの自動注入（コンテナにトークンを渡さない）
- 全リクエストの監査ログ出力
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from app.infrastructure.audit_log import (
    audit_mcp_proxy_request,
    audit_proxy_request_allowed,
    audit_proxy_request_blocked,
)
from app.infrastructure.metrics import (
    get_workspace_proxy_blocked,
    get_workspace_proxy_request_duration,
)
from app.services.proxy.dns_cache import DNSCache
from app.services.proxy.domain_whitelist import DomainWhitelist
from app.services.proxy.sigv4 import AWSCredentials, sign_request

logger = structlog.get_logger(__name__)

# MCP リバースプロキシのパスプレフィックス
MCP_PROXY_PREFIX = "/mcp/"


@dataclass
class McpHeaderRule:
    """MCPサーバー用ヘッダー注入ルール"""

    real_base_url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ProxyConfig:
    """Proxy設定"""

    whitelist_domains: list[str]
    aws_credentials: AWSCredentials
    log_all_requests: bool = True


class CredentialInjectionProxy:
    """
    Unix Socket上で動作するHTTP Proxy（Forward + Reverse 兼用）

    3つのモードで動作:
    1. Reverse Proxy (Bedrock): ANTHROPIC_BEDROCK_BASE_URL からの直接リクエスト（相対パス）
       → Bedrock API URLを構築し、SigV4署名を注入して転送（ストリーミング対応）
    2. Reverse Proxy (MCP): /mcp/{server_name}/... パスのリクエスト
       → 認証ヘッダーを注入し、実際のMCP APIエンドポイントに転送
    3. Forward Proxy: HTTP_PROXY/HTTPS_PROXY からのプロキシリクエスト（絶対URL/CONNECT）
       → 許可ドメインのみ通信許可、bedrock-runtime にはSigV4認証を自動注入
    """

    def __init__(self, config: ProxyConfig, socket_path: str) -> None:
        self.config = config
        self.socket_path = socket_path
        self._whitelist = DomainWhitelist(config.whitelist_domains)
        self._dns_cache = DNSCache(ttl_seconds=300)
        self._http_client: httpx.AsyncClient | None = None
        self._server: asyncio.AbstractServer | None = None
        self._mcp_header_rules: dict[str, McpHeaderRule] = {}

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

    def update_mcp_header_rules(self, rules: dict[str, McpHeaderRule]) -> None:
        """MCPヘッダー注入ルールを更新（実行リクエスト毎に呼ばれる）

        Args:
            rules: {server_name: McpHeaderRule} のマッピング
        """
        self._mcp_header_rules = rules
        if rules:
            logger.info(
                "MCPヘッダールール更新",
                server_names=list(rules.keys()),
            )

    async def stop(self) -> None:
        """Proxyサーバーを停止"""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._http_client:
            await self._http_client.aclose()
        self._mcp_header_rules = {}
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

            # Reverse Proxy モード: 相対パス（ANTHROPIC_BEDROCK_BASE_URL経由）
            if url.startswith(MCP_PROXY_PREFIX):
                # MCP Reverse Proxy: /mcp/{server_name}/... パスのリクエスト
                # コンテナにトークンを渡さず、プロキシ側で認証ヘッダーを注入
                await self._handle_mcp_reverse_proxy(method, url, headers, body, writer)
                return

            # SDK が ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8080 で送信するリクエストは
            # 相対パス（例: /model/{modelId}/invoke）で届く
            if url.startswith("/"):
                await self._handle_bedrock_reverse_proxy(
                    method, url, headers, body, writer
                )
                return

            # Forward Proxy モード: 絶対URL（HTTP_PROXY/HTTPS_PROXY経由）
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
                logger.debug("Writer close失敗", exc_info=True)

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
            audit_proxy_request_blocked(method=method, url=url)
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
        audit_proxy_request_allowed(
            method=method,
            url=url,
            status=result[0],
            duration_ms=int(duration * 1000),
        )
        if self.config.log_all_requests:
            logger.info(
                "Proxy: 完了",
                method=method,
                url=url,
                duration_ms=round(duration * 1000, 1),
                status=result[0],
            )

        return result

    async def _handle_bedrock_reverse_proxy(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        Bedrock Reverse Proxy: 相対パスリクエストをBedrock APIに転送

        ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8080 経由で届いたリクエストを
        実際のBedrock APIエンドポイントに転送する。
        SigV4署名を注入し、レスポンスはストリーミングで返す。
        """
        request_start = time.perf_counter()
        region = self.config.aws_credentials.region
        bedrock_url = f"https://bedrock-runtime.{region}.amazonaws.com{path}"

        if self.config.log_all_requests:
            logger.info(
                "Proxy: Bedrock Reverse Proxy",
                method=method,
                path=path,
                bedrock_url=bedrock_url,
            )

        # Hop-by-hop ヘッダーを除去し、Host を設定
        forward_headers = {
            k: v
            for k, v in headers.items()
            if k.lower()
            not in (
                "host",
                "connection",
                "proxy-connection",
                "keep-alive",
                "transfer-encoding",
            )
        }
        forward_headers["Host"] = f"bedrock-runtime.{region}.amazonaws.com"

        # SigV4署名を注入
        signed_headers = sign_request(
            credentials=self.config.aws_credentials,
            method=method,
            url=bedrock_url,
            headers=forward_headers,
            body=body,
            service="bedrock",
        )

        if not self._http_client:
            writer.write(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 22\r\n\r\nProxy not initialized\r\n"
            )
            await writer.drain()
            return

        try:
            # ストリーミングレスポンスでBedrock APIに転送
            async with self._http_client.stream(
                method=method,
                url=bedrock_url,
                headers=signed_headers,
                content=body,
            ) as resp:
                # レスポンスステータス行
                status_text = "OK" if resp.status_code < 400 else "Error"
                writer.write(f"HTTP/1.1 {resp.status_code} {status_text}\r\n".encode())

                # レスポンスヘッダー（Content-Lengthがあればそのまま、なければchunked）
                has_content_length = False
                for key, value in resp.headers.multi_items():
                    lower_key = key.lower()
                    if lower_key in ("transfer-encoding", "connection"):
                        continue
                    if lower_key == "content-length":
                        has_content_length = True
                    writer.write(f"{key}: {value}\r\n".encode())

                if not has_content_length:
                    writer.write(b"Transfer-Encoding: chunked\r\n")

                writer.write(b"\r\n")
                await writer.drain()

                # レスポンスボディをストリーミング
                if has_content_length:
                    # Content-Length がある場合はそのまま転送
                    async for chunk in resp.aiter_bytes():
                        writer.write(chunk)
                        await writer.drain()
                else:
                    # chunked transfer encoding
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            writer.write(f"{len(chunk):x}\r\n".encode())
                            writer.write(chunk)
                            writer.write(b"\r\n")
                            await writer.drain()
                    writer.write(b"0\r\n\r\n")
                    await writer.drain()

            # メトリクス・監査ログ
            duration = time.perf_counter() - request_start
            get_workspace_proxy_request_duration().observe(duration, method=method)
            audit_proxy_request_allowed(
                method=method,
                url=bedrock_url,
                status=resp.status_code,
                duration_ms=int(duration * 1000),
            )
            if self.config.log_all_requests:
                logger.info(
                    "Proxy: Bedrock完了",
                    method=method,
                    path=path,
                    status=resp.status_code,
                    duration_ms=round(duration * 1000, 1),
                )

        except httpx.TimeoutException:
            logger.error("Proxy: Bedrockタイムアウト", method=method, path=path)
            writer.write(
                b"HTTP/1.1 504 Gateway Timeout\r\nContent-Length: 15\r\n\r\nGateway Timeout"
            )
            await writer.drain()
        except Exception as e:
            logger.error(
                "Proxy: Bedrock転送エラー", method=method, path=path, error=str(e)
            )
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 11\r\n\r\nBad Gateway"
            )
            await writer.drain()

    async def _handle_mcp_reverse_proxy(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        MCP Reverse Proxy: /mcp/{server_name}/... パスのリクエストを実際のMCP APIに転送

        コンテナ内のOpenAPIMcpServiceが http://127.0.0.1:8080/mcp/{server_name}/api/...
        に送信するリクエストを、実際のMCP APIエンドポイントに転送する。
        プロキシ側で認証ヘッダーを注入し、コンテナにトークンを渡さない。
        """
        request_start = time.perf_counter()

        # /mcp/{server_name}/remaining/path からserver_nameとパスを抽出
        mcp_path = path[len(MCP_PROXY_PREFIX) :]
        slash_idx = mcp_path.find("/")
        if slash_idx == -1:
            server_name = mcp_path
            remaining_path = ""
        else:
            server_name = mcp_path[:slash_idx]
            remaining_path = mcp_path[slash_idx:]

        # MCPヘッダールールの検索
        rule = self._mcp_header_rules.get(server_name)
        if not rule:
            logger.warning(
                "Proxy: 未知のMCPサーバー", server_name=server_name, path=path
            )
            error_body = f"Unknown MCP server: {server_name}".encode()
            writer.write(
                f"HTTP/1.1 404 Not Found\r\nContent-Length: {len(error_body)}\r\n\r\n".encode()
            )
            writer.write(error_body)
            await writer.drain()
            return

        # 実際のMCP APIのURLを構築
        target_url = f"{rule.real_base_url.rstrip('/')}{remaining_path}"

        if self.config.log_all_requests:
            logger.info(
                "Proxy: MCP Reverse Proxy",
                method=method,
                server_name=server_name,
                target_url=target_url,
            )

        # Hop-by-hop ヘッダーを除去
        forward_headers = {
            k: v
            for k, v in headers.items()
            if k.lower()
            not in (
                "host",
                "connection",
                "proxy-connection",
                "keep-alive",
                "transfer-encoding",
            )
        }

        # 実際のMCP APIのHostヘッダーを設定
        parsed = urlparse(target_url)
        if parsed.hostname:
            forward_headers["Host"] = parsed.hostname

        # MCPサーバー用の認証ヘッダーを注入（コンテナから受け取らず、プロキシ側で保持）
        forward_headers.update(rule.headers)

        if not self._http_client:
            writer.write(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Length: 22\r\n\r\nProxy not initialized\r\n"
            )
            await writer.drain()
            return

        try:
            resp = await self._http_client.request(
                method=method,
                url=target_url,
                headers=forward_headers,
                content=body,
            )

            # レスポンス送信
            status_text = "OK" if resp.status_code < 400 else "Error"
            writer.write(f"HTTP/1.1 {resp.status_code} {status_text}\r\n".encode())

            resp_body = resp.content
            for k, v in resp.headers.multi_items():
                lower_key = k.lower()
                if lower_key in ("transfer-encoding", "connection", "content-length"):
                    continue
                writer.write(f"{k}: {v}\r\n".encode())

            writer.write(f"Content-Length: {len(resp_body)}\r\n".encode())
            writer.write(b"\r\n")
            writer.write(resp_body)
            await writer.drain()

            # メトリクス・監査ログ
            duration = time.perf_counter() - request_start
            get_workspace_proxy_request_duration().observe(duration, method=method)
            audit_mcp_proxy_request(
                server_name=server_name,
                method=method,
                path=remaining_path,
                status=resp.status_code,
                duration_ms=int(duration * 1000),
            )
            if self.config.log_all_requests:
                logger.info(
                    "Proxy: MCP完了",
                    method=method,
                    server_name=server_name,
                    status=resp.status_code,
                    duration_ms=round(duration * 1000, 1),
                )

        except httpx.TimeoutException:
            logger.error(
                "Proxy: MCPタイムアウト",
                method=method,
                server_name=server_name,
            )
            writer.write(
                b"HTTP/1.1 504 Gateway Timeout\r\n"
                b"Content-Length: 15\r\n\r\nGateway Timeout"
            )
            await writer.drain()
        except Exception as e:
            logger.error(
                "Proxy: MCP転送エラー",
                method=method,
                server_name=server_name,
                error=str(e),
            )
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 11\r\n\r\nBad Gateway"
            )
            await writer.drain()

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
            writer.write(
                b"HTTP/1.1 403 Forbidden\r\nContent-Length: 26\r\n\r\nDomain not in whitelist\r\n"
            )
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
                logger.debug("ストリームパイプ失敗", exc_info=True)

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
                    logger.debug("リモートWriter close失敗", exc_info=True)

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
