"""
API認証ミドルウェア

内部通信用のAPI Key認証を提供
フロントエンドサーバーとの通信を保護
"""
import hmac
import hashlib
from typing import Callable, Optional
from urllib.parse import urlparse

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class AuthMiddleware(BaseHTTPMiddleware):
    """
    API認証ミドルウェア

    内部通信用のAPI Key認証を実装
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

    def __init__(self, app, api_keys: list[str]):
        """
        初期化

        Args:
            app: FastAPIアプリケーション
            api_keys: 許可するAPIキーのリスト
        """
        super().__init__(app)
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

    def _extract_api_key(self, request: Request) -> Optional[str]:
        """
        リクエストからAPIキーを抽出

        以下の順序で検索:
        1. X-API-Key ヘッダー
        2. Authorization: Bearer <key> ヘッダー
        """
        # X-API-Key ヘッダー
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return api_key

        # Authorization ヘッダー
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]

        return None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """リクエストを処理"""
        # 認証が無効の場合はスキップ
        if not self.enabled:
            return await call_next(request)

        # 認証スキップパスのチェック
        if self._should_skip_auth(request.url.path):
            return await call_next(request)

        # APIキーの抽出と検証
        api_key = self._extract_api_key(request)

        if not api_key:
            logger.warning(
                "APIキーが提供されていません",
                path=request.url.path,
                client_ip=request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "APIキーが必要です",
                    }
                },
            )

        if not self._verify_key(api_key):
            logger.warning(
                "無効なAPIキー",
                path=request.url.path,
                client_ip=request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "無効なAPIキーです",
                    }
                },
            )

        return await call_next(request)
