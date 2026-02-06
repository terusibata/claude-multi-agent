"""
API認証ミドルウェア

内部通信用のAPI Key認証を提供
フロントエンドサーバーとの通信を保護

純粋なASGIミドルウェアとして実装し、SSEストリーミングとの互換性を確保
"""
import hashlib
import hmac
import json
from typing import Optional

import structlog
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class AuthMiddleware:
    """
    API認証ミドルウェア（純粋なASGI実装）

    内部通信用のAPI Key認証を実装。
    BaseHTTPMiddlewareを使わないことで、SSEストリーミングレスポンスとの
    互換性問題を回避する。
    """

    # 認証をスキップするパス
    SKIP_AUTH_PATHS = {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
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
        """APIキーを検証（タイミング攻撃対策）"""
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

    def _extract_api_key(self, scope: Scope) -> Optional[str]:
        """scopeのヘッダーからAPIキーを抽出"""
        headers = dict(
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in scope.get("headers", [])
        )

        # X-API-Key ヘッダー
        api_key = headers.get("x-api-key")
        if api_key:
            return api_key

        # Authorization ヘッダー
        auth_header = headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]

        return None

    async def _send_json_response(self, send: Send, status_code: int, body: dict) -> None:
        """JSON レスポンスを直接送信"""
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body_bytes)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body_bytes,
        })

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 認証が無効の場合はスキップ
        if not self.enabled:
            await self.app(scope, receive, send)
            return

        # 認証スキップパスのチェック
        path = scope.get("path", "")
        if self._should_skip_auth(path):
            await self.app(scope, receive, send)
            return

        # APIキーの抽出と検証
        api_key = self._extract_api_key(scope)

        if not api_key:
            logger.warning(
                "APIキーが提供されていません",
                path=path,
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
            )
            await self._send_json_response(send, 401, {
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "無効なAPIキーです",
                }
            })
            return

        await self.app(scope, receive, send)
