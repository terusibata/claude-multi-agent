"""
レート制限ミドルウェア

Redisベースのスライディングウィンドウアルゴリズムによるレート制限
AI実行系API（一般ユーザー向け）のみに適用
"""
import re
import time
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings
from app.infrastructure.redis import redis_client

logger = structlog.get_logger(__name__)
settings = get_settings()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    レート制限ミドルウェア

    AI実行系API（一般ユーザー向け）のみにレート制限を適用:
    - 会話関連: /api/tenants/{tenant_id}/conversations/**
    - ワークスペース関連: /api/tenants/{tenant_id}/conversations/{id}/files/**

    管理系API（管理者向け）はレート制限をスキップ:
    - テナント管理: /api/tenants
    - モデル管理: /api/models
    - スキル管理: /api/tenants/{tenant_id}/skills
    - MCPサーバー管理: /api/tenants/{tenant_id}/mcp-servers
    - 使用状況: /api/tenants/{tenant_id}/usage
    """

    # レート制限を常にスキップするパス
    ALWAYS_SKIP_PATHS = {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
    }

    # レート制限を適用するパスパターン（正規表現）
    # AI実行系API（一般ユーザー向け）
    RATE_LIMITED_PATTERNS = [
        # 会話関連（作成、一覧、詳細、メッセージ、ストリーミング）
        re.compile(r"^/api/tenants/[^/]+/conversations(?:/[^/]+)?(?:/messages|/stream)?$"),
        # ワークスペース関連（ファイル操作）
        re.compile(r"^/api/tenants/[^/]+/conversations/[^/]+/files(?:/.*)?$"),
    ]

    # Luaスクリプト: スライディングウィンドウカウンター
    RATE_LIMIT_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local window_start = now - window

    -- 古いエントリを削除
    redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

    -- 現在のカウントを取得
    local count = redis.call('ZCARD', key)

    if count < limit then
        -- リクエストを記録
        redis.call('ZADD', key, now, now .. ':' .. math.random())
        redis.call('EXPIRE', key, window)
        return {1, limit - count - 1, window}
    else
        -- レート制限に到達
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local retry_after = 0
        if oldest and #oldest >= 2 then
            retry_after = math.ceil(oldest[2] + window - now)
        end
        return {0, 0, retry_after}
    end
    """

    def __init__(
        self,
        app,
        requests_per_window: int,
        window_seconds: int,
        key_prefix: str = "ratelimit:",
    ):
        """
        初期化

        Args:
            app: FastAPIアプリケーション
            requests_per_window: ウィンドウあたりの最大リクエスト数
            window_seconds: ウィンドウのサイズ（秒）
            key_prefix: Redisキーのプレフィックス
        """
        super().__init__(app)
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.enabled = settings.rate_limit_enabled

        if not self.enabled:
            logger.info("レート制限が無効化されています")

    def _get_rate_limit_key(self, request: Request) -> str:
        """
        レート制限キーを取得

        AI実行系APIはユーザー単位で制限:
        - X-User-ID + X-Tenant-ID: ユーザー単位（必須）
        - ヘッダーなし: IP単位（フォールバック）
        """
        user_id = request.headers.get("X-User-ID")
        tenant_id = request.headers.get("X-Tenant-ID")

        # ユーザーIDとテナントIDがあればユーザー単位で制限
        if user_id and tenant_id:
            return f"{self.key_prefix}user:{tenant_id}:{user_id}"

        # フォールバック: IP単位
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
            return f"{self.key_prefix}ip:{client_ip}"

        if request.client:
            return f"{self.key_prefix}ip:{request.client.host}"

        return f"{self.key_prefix}ip:unknown"

    def _should_apply_rate_limit(self, path: str) -> bool:
        """
        レート制限を適用すべきパスか判定

        Returns:
            True: レート制限を適用
            False: レート制限をスキップ
        """
        # 常にスキップするパス
        if path in self.ALWAYS_SKIP_PATHS:
            return False

        # AI実行系パターンにマッチするか確認
        for pattern in self.RATE_LIMITED_PATTERNS:
            if pattern.match(path):
                return True

        # それ以外（管理系API）はスキップ
        return False

    async def _check_rate_limit(
        self,
        key: str,
    ) -> tuple[bool, int, int]:
        """
        レート制限をチェック

        Returns:
            (allowed: bool, remaining: int, retry_after: int)
        """
        now = time.time()

        try:
            async with redis_client() as redis:
                result = await redis.eval(
                    self.RATE_LIMIT_SCRIPT,
                    1,
                    key,
                    now,
                    self.window_seconds,
                    self.requests_per_window,
                )

                allowed = result[0] == 1
                remaining = max(0, result[1])
                retry_after = result[2] if not allowed else 0

                return allowed, remaining, retry_after

        except Exception as e:
            # Redisエラー時は通過を許可（フェイルオープン）
            logger.error("レート制限チェック失敗", error=str(e))
            return True, self.requests_per_window, 0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """リクエストを処理"""
        # レート制限が無効の場合はスキップ
        if not self.enabled:
            return await call_next(request)

        # レート制限を適用すべきか判定
        if not self._should_apply_rate_limit(request.url.path):
            return await call_next(request)

        # レート制限キーを取得
        rate_limit_key = self._get_rate_limit_key(request)

        # レート制限チェック
        allowed, remaining, retry_after = await self._check_rate_limit(rate_limit_key)

        if not allowed:
            logger.warning(
                "レート制限超過",
                rate_limit_key=rate_limit_key,
                path=request.url.path,
                retry_after=retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "リクエスト数が制限を超えました。しばらくしてから再試行してください。",
                        "retry_after": retry_after,
                    }
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.requests_per_window),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                },
            )

        # リクエストを処理
        response = await call_next(request)

        # レート制限ヘッダーを追加
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_window)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(
            int(time.time()) + self.window_seconds
        )

        return response
