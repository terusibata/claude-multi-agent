"""
レート制限ミドルウェア

Redisベースのスライディングウィンドウアルゴリズムによるレート制限
ユーザー単位 + テナント単位の階層的レート制限
"""
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

    階層的なレート制限を実装:
    1. ユーザー単位: 個人の乱用防止（固定値）
    2. テナント単位: テナント全体の制限（DB設定値 or デフォルト）
    3. IP単位: ヘッダーがない場合のフォールバック

    攻撃対策用のため、正常利用では引っかからない緩めの設定を推奨
    """

    # レート制限をスキップするパス
    SKIP_RATE_LIMIT_PATHS = {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
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
            requests_per_window: ユーザー単位のウィンドウあたり最大リクエスト数
            window_seconds: ウィンドウのサイズ（秒）
            key_prefix: Redisキーのプレフィックス
        """
        super().__init__(app)
        self.user_requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.enabled = settings.rate_limit_enabled

        # テナント単位のデフォルト制限（ユーザー制限の30倍程度）
        self.tenant_requests_per_window = requests_per_window * 30

        if not self.enabled:
            logger.info("レート制限が無効化されています")

    def _get_identifiers(self, request: Request) -> tuple[str | None, str | None, str]:
        """
        クライアント識別子を取得

        Returns:
            (user_id, tenant_id, ip_address)
        """
        user_id = request.headers.get("X-User-ID")
        tenant_id = request.headers.get("X-Tenant-ID")

        # IPアドレス取得
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ip_address = forwarded_for.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host
        else:
            ip_address = "unknown"

        return user_id, tenant_id, ip_address

    def _should_skip_rate_limit(self, path: str) -> bool:
        """レート制限をスキップすべきパスか判定"""
        return path in self.SKIP_RATE_LIMIT_PATHS

    async def _check_rate_limit(
        self,
        key: str,
        limit: int,
    ) -> tuple[bool, int, int]:
        """
        レート制限をチェック

        Args:
            key: Redisキー
            limit: リクエスト上限

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
                    limit,
                )

                allowed = result[0] == 1
                remaining = max(0, result[1])
                retry_after = result[2] if not allowed else 0

                return allowed, remaining, retry_after

        except Exception as e:
            # Redisエラー時は通過を許可（フェイルオープン）
            logger.error("レート制限チェック失敗", error=str(e))
            return True, limit, 0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """リクエストを処理"""
        # レート制限が無効の場合はスキップ
        if not self.enabled:
            return await call_next(request)

        # スキップパスのチェック
        if self._should_skip_rate_limit(request.url.path):
            return await call_next(request)

        # 識別子を取得
        user_id, tenant_id, ip_address = self._get_identifiers(request)

        # 使用する制限値とキーを決定
        rate_limit_key: str
        rate_limit_value: int
        limit_type: str

        if user_id and tenant_id:
            # ユーザー単位の制限を適用（最も細かい粒度）
            rate_limit_key = f"{self.key_prefix}user:{tenant_id}:{user_id}"
            rate_limit_value = self.user_requests_per_window
            limit_type = "user"
        elif tenant_id:
            # テナント単位の制限を適用
            rate_limit_key = f"{self.key_prefix}tenant:{tenant_id}"
            rate_limit_value = self.tenant_requests_per_window
            limit_type = "tenant"
        else:
            # IP単位の制限を適用（フォールバック）
            rate_limit_key = f"{self.key_prefix}ip:{ip_address}"
            rate_limit_value = self.user_requests_per_window
            limit_type = "ip"

        # レート制限チェック
        allowed, remaining, retry_after = await self._check_rate_limit(
            rate_limit_key, rate_limit_value
        )

        if not allowed:
            logger.warning(
                "レート制限超過",
                limit_type=limit_type,
                user_id=user_id,
                tenant_id=tenant_id,
                ip_address=ip_address,
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
                    "X-RateLimit-Limit": str(rate_limit_value),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                },
            )

        # リクエストを処理
        response = await call_next(request)

        # レート制限ヘッダーを追加
        response.headers["X-RateLimit-Limit"] = str(rate_limit_value)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(
            int(time.time()) + self.window_seconds
        )

        return response
