"""
レート制限ミドルウェア

Redisベースのスライディングウィンドウアルゴリズムによるレート制限
"""
import time
from typing import Callable, Optional

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

    スライディングウィンドウアルゴリズムを使用
    """

    # レート制限をスキップするパス
    SKIP_RATE_LIMIT_PATHS = {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
    }

    # Luaスクリプト: スライディングウィンドウカウンター
    # アトミックな操作でレート制限を実装
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

    def _get_client_identifier(self, request: Request) -> str:
        """
        クライアント識別子を取得

        優先順位:
        1. X-Tenant-ID ヘッダー（テナント単位の制限）
        2. X-Forwarded-For ヘッダー（プロキシ経由）
        3. クライアントIP
        """
        # テナントIDがあればテナント単位で制限
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            return f"tenant:{tenant_id}"

        # X-Forwarded-For（最初のIPを使用）
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
            return f"ip:{client_ip}"

        # 直接接続のクライアントIP
        if request.client:
            return f"ip:{request.client.host}"

        return "ip:unknown"

    def _should_skip_rate_limit(self, path: str) -> bool:
        """レート制限をスキップすべきパスか判定"""
        return path in self.SKIP_RATE_LIMIT_PATHS

    async def _check_rate_limit(
        self,
        client_id: str,
    ) -> tuple[bool, int, int]:
        """
        レート制限をチェック

        Returns:
            (allowed: bool, remaining: int, retry_after: int)
        """
        key = f"{self.key_prefix}{client_id}"
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

        # スキップパスのチェック
        if self._should_skip_rate_limit(request.url.path):
            return await call_next(request)

        # クライアント識別子を取得
        client_id = self._get_client_identifier(request)

        # レート制限チェック
        allowed, remaining, retry_after = await self._check_rate_limit(client_id)

        if not allowed:
            logger.warning(
                "レート制限超過",
                client_id=client_id,
                path=request.url.path,
                retry_after=retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "リクエスト数が制限を超えました",
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
