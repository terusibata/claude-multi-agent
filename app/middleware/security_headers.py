"""
セキュリティヘッダーミドルウェア

OWASPセキュリティヘッダー推奨に基づいた実装

純粋なASGIミドルウェアとして実装し、SSEストリーミングとの互換性を確保
"""
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """
    セキュリティヘッダーミドルウェア（純粋なASGI実装）

    以下のセキュリティヘッダーを追加:
    - X-Content-Type-Options: MIME Sniffing対策
    - X-Frame-Options: クリックジャッキング対策
    - X-XSS-Protection: XSS対策（レガシーブラウザ向け）
    - Strict-Transport-Security: HTTPS強制
    - Content-Security-Policy: CSP
    - Referrer-Policy: Referrer情報の制御
    - Permissions-Policy: ブラウザ機能の制限
    """

    # ドキュメントエンドポイント（Swagger UI用にCSPを緩和）
    DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

    # Swagger UI用のCSP
    DOCS_CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "frame-ancestors 'none'"
    )

    # デフォルトのセキュリティヘッダー
    DEFAULT_HEADERS = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "x-xss-protection": "1; mode=block",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "geolocation=(), microphone=(), camera=()",
        "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate",
        "pragma": "no-cache",
        "expires": "0",
    }

    def __init__(
        self,
        app: ASGIApp,
        enable_hsts: bool = True,
        hsts_max_age: int = 31536000,
        hsts_include_subdomains: bool = True,
        enable_csp: bool = True,
        custom_csp: str | None = None,
    ):
        self.app = app
        self.headers: dict[str, str] = dict(self.DEFAULT_HEADERS)

        # HSTS設定
        if enable_hsts:
            hsts_value = f"max-age={hsts_max_age}"
            if hsts_include_subdomains:
                hsts_value += "; includeSubDomains"
            self.headers["strict-transport-security"] = hsts_value

        # CSP設定
        if enable_csp:
            if custom_csp:
                self.headers["content-security-policy"] = custom_csp
            else:
                self.headers["content-security-policy"] = (
                    "default-src 'none'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'none'; "
                    "form-action 'none'"
                )

        # バイト化されたヘッダーリストを事前計算
        self._header_bytes = [
            [k.encode("latin-1"), v.encode("latin-1")]
            for k, v in self.headers.items()
        ]
        self._docs_csp_bytes = self.DOCS_CSP.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_docs = path in self.DOCS_PATHS

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing_headers = list(message.get("headers", []))
                existing_header_names = {
                    h[0].decode("latin-1").lower() if isinstance(h[0], bytes) else h[0].lower()
                    for h in existing_headers
                }

                for header_name_bytes, header_value_bytes in self._header_bytes:
                    header_name = header_name_bytes.decode("latin-1")
                    if header_name not in existing_header_names:
                        # ドキュメントエンドポイントにはSwagger UI用のCSPを適用
                        if header_name == "content-security-policy" and is_docs:
                            existing_headers.append([header_name_bytes, self._docs_csp_bytes])
                        else:
                            existing_headers.append([header_name_bytes, header_value_bytes])

                message = {**message, "headers": existing_headers}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
