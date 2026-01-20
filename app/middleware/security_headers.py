"""
セキュリティヘッダーミドルウェア

OWASPセキュリティヘッダー推奨に基づいた実装
"""
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    セキュリティヘッダーミドルウェア

    以下のセキュリティヘッダーを追加:
    - X-Content-Type-Options: MIME Sniffing対策
    - X-Frame-Options: クリックジャッキング対策
    - X-XSS-Protection: XSS対策（レガシーブラウザ向け）
    - Strict-Transport-Security: HTTPS強制
    - Content-Security-Policy: CSP
    - Referrer-Policy: Referrer情報の制御
    - Permissions-Policy: ブラウザ機能の制限
    """

    # デフォルトのセキュリティヘッダー
    DEFAULT_HEADERS = {
        # MIME Sniffing対策
        "X-Content-Type-Options": "nosniff",
        # クリックジャッキング対策
        "X-Frame-Options": "DENY",
        # XSS対策（レガシーブラウザ向け）
        "X-XSS-Protection": "1; mode=block",
        # Referrer情報の制御
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # ブラウザ機能の制限（API用のため厳格に制限）
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        # キャッシュ制御（APIレスポンスのキャッシュを防止）
        "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    def __init__(
        self,
        app,
        enable_hsts: bool = True,
        hsts_max_age: int = 31536000,  # 1年
        hsts_include_subdomains: bool = True,
        enable_csp: bool = True,
        custom_csp: str | None = None,
    ):
        """
        初期化

        Args:
            app: FastAPIアプリケーション
            enable_hsts: HSTSを有効化するか
            hsts_max_age: HSTSのmax-age（秒）
            hsts_include_subdomains: サブドメインを含めるか
            enable_csp: CSPを有効化するか
            custom_csp: カスタムCSPポリシー
        """
        super().__init__(app)
        self.headers = dict(self.DEFAULT_HEADERS)

        # HSTS設定
        if enable_hsts:
            hsts_value = f"max-age={hsts_max_age}"
            if hsts_include_subdomains:
                hsts_value += "; includeSubDomains"
            self.headers["Strict-Transport-Security"] = hsts_value

        # CSP設定
        if enable_csp:
            if custom_csp:
                self.headers["Content-Security-Policy"] = custom_csp
            else:
                # APIサーバー用のデフォルトCSP
                self.headers["Content-Security-Policy"] = (
                    "default-src 'none'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'none'; "
                    "form-action 'none'"
                )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """リクエストを処理"""
        response = await call_next(request)

        # セキュリティヘッダーを追加
        for header, value in self.headers.items():
            # 既存のヘッダーは上書きしない
            if header not in response.headers:
                response.headers[header] = value

        return response
