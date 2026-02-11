"""
API認証ミドルウェア

内部通信用のAPI Key認証を提供
フロントエンドサーバーとの通信を保護

Pure ASGIミドルウェアとして実装（SSEストリーミング対応）
"""
import hashlib
import hmac
import json

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger(__name__)


class AuthMiddleware:
    """
    API認証ミドルウェア（Pure ASGI実装）

    内部通信用のAPI Key認証を実装。
    BaseHTTPMiddlewareを使わず、SSEストリーミングとの互換性を確保。
    """

    # 認証をスキップするパス
    SKIP_AUTH_PATHS = {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
    }

    # 認証をスキップするパスのプレフィックス
    SKIP_AUTH_PREFIXES = (
        "/docs",
        "/redoc",
    )

    def __init__(self, app: ASGIApp, api_keys: list[str]):
        """
        初期化

        Args:
            app: 次のASGIアプリケーション
            api_keys: 許可するAPIキーのリスト
        """
        self.app = app
        # APIキーのハッシュを保存（タイミング攻撃対策）
        self.api_key_hashes = {
            self._hash_key(key) for key in api_keys if key
        }
        self.enabled = bool(self.api_key_hashes)

        if not self.enabled:
            logger.warning(
                "API認証が無効化されています",
                reason="API_KEYSが設定されていません",
            )

    def _hash_key(self, key: str) -> str:
        """APIキーをハッシュ化"""
        return hashlib.sha256(key.encode()).hexdigest()

    def _verify_key(self, provided_key: str) -> bool:
        """
        APIキーを検証

        タイミング攻撃を防ぐためhmac.compare_digestを使用
        """
        provided_hash = self._hash_key(provided_key)
        for stored_hash in self.api_key_hashes:
            if hmac.compare_digest(provided_hash, stored_hash):
                return True
        return False

    def _should_skip_auth(self, path: str) -> bool:
        """認証をスキップすべきパスか判定"""
        if path in self.SKIP_AUTH_PATHS:
            return True
        if path.startswith(self.SKIP_AUTH_PREFIXES):
            return True
        return False

    def _get_header(self, scope: Scope, name: bytes) -> str | None:
        """スコープからヘッダー値を取得"""
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == name:
                return header_value.decode("latin-1")
        return None

    def _extract_api_key(self, scope: Scope) -> str | None:
        """
        リクエストスコープからAPIキーを抽出

        以下の順序で検索:
        1. X-API-Key ヘッダー
        2. Authorization: Bearer <key> ヘッダー
        """
        # X-API-Key ヘッダー
        api_key = self._get_header(scope, b"x-api-key")
        if api_key:
            return api_key

        # Authorization ヘッダー
        auth_header = self._get_header(scope, b"authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]

        return None

    def _get_client_ip(self, scope: Scope) -> str:
        """クライアントIPを取得"""
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def _send_json_response(self, send: Send, status: int, body: dict) -> None:
        """JSONレスポンスを送信"""
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body_bytes,
        })

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 認証が無効の場合はスキップ
        if not self.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # 認証スキップパスのチェック
        if self._should_skip_auth(path):
            await self.app(scope, receive, send)
            return

        # APIキーの抽出と検証
        api_key = self._extract_api_key(scope)

        if not api_key:
            logger.warning(
                "APIキーが提供されていません",
                path=path,
                client_ip=self._get_client_ip(scope),
            )
            await self._send_json_response(send, 401, {
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "APIキーが必要です",
                }
            })
            return

        if not self._verify_key(api_key):
            logger.warning(
                "無効なAPIキー",
                path=path,
                client_ip=self._get_client_ip(scope),
            )
            await self._send_json_response(send, 401, {
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "無効なAPIキーです",
                }
            })
            return

        await self.app(scope, receive, send)
