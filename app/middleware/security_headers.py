"""
セキュリティヘッダーミドルウェア

OWASPセキュリティヘッダー推奨に基づいた実装

Pure ASGIミドルウェアとして実装（SSEストリーミング対応）
"""
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """
    セキュリティヘッダーミドルウェア（Pure ASGI実装）

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

    # Swagger UI用のCSP（FastAPIはCDNからリソースを読み込む）
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
        # MIME Sniffing対策
        "x-content-type-options": "nosniff",
        # クリックジャッキング対策
        "x-frame-options": "DENY",
        # XSS対策（レガシーブラウザ向け）
        "x-xss-protection": "1; mode=block",
        # Referrer情報の制御
        "referrer-policy": "strict-origin-when-cross-origin",
        # ブラウザ機能の制限（API用のため厳格に制限）
        "permissions-policy": "geolocation=(), microphone=(), camera=()",
        # キャッシュ制御（APIレスポンスのキャッシュを防止）
        "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate",
        "pragma": "no-cache",
        "expires": "0",
    }

    def __init__(
        self,
        app: ASGIApp,
        enable_hsts: bool = True,
        hsts_max_age: int = 31536000,  # 1年
        hsts_include_subdomains: bool = True,
        enable_csp: bool = True,
        custom_csp: str | None = None,
    ):
        """
        初期化

        Args:
            app: 次のASGIアプリケーション
            enable_hsts: HSTSを有効化するか
            hsts_max_age: HSTSのmax-age（秒）
            hsts_include_subdomains: サブドメインを含めるか
            enable_csp: CSPを有効化するか
            custom_csp: カスタムCSPポリシー
        """
        self.app = app
        self.headers: dict[str, str] = dict(self.DEFAULT_HEADERS)

        # HSTS設定
        if enable_hsts:
            hsts_value = f"max-age={hsts_max_age}"
            if hsts_include_subdomains:
                hsts_value += "; includeSubDomains"
            self.headers["strict-transport-security"] = hsts_value

        # CSP設定
        self.default_csp: str | None = None
        if enable_csp:
            if custom_csp:
                self.default_csp = custom_csp
            else:
                # APIサーバー用のデフォルトCSP
                self.default_csp = (
                    "default-src 'none'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'none'; "
                    "form-action 'none'"
                )

        # ヘッダーをバイト列ペアのリストとしてプリコンパイル
        self._header_pairs: list[list[bytes]] = [
            [k.encode(), v.encode()] for k, v in self.headers.items()
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_docs = path in self.DOCS_PATHS

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing_names = {
                    h[0].lower() if isinstance(h[0], bytes) else h[0].encode().lower()
                    for h in headers
                }

                for header_pair in self._header_pairs:
                    if header_pair[0] not in existing_names:
                        headers.append(header_pair)

                # CSPヘッダー追加
                if self.default_csp and b"content-security-policy" not in existing_names:
                    if is_docs:
                        headers.append([b"content-security-policy", self.DOCS_CSP.encode()])
                    else:
                        headers.append([b"content-security-policy", self.default_csp.encode()])

                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_with_security_headers)
