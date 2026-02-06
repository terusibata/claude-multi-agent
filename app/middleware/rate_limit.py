"""
レート制限ミドルウェア

Redisベースのスライディングウィンドウアルゴリズムによるレート制限
AI実行系API（一般ユーザー向け）のみに適用

純粋なASGIミドルウェアとして実装し、SSEストリーミングとの互換性を確保
"""
import json
import re
import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.infrastructure.redis import redis_client

logger = structlog.get_logger(__name__)
settings = get_settings()


class RateLimitMiddleware:
    """
    レート制限ミドルウェア（純粋なASGI実装）

    AI実行系API（一般ユーザー向け）のみにレート制限を適用:
    - 会話関連: /api/tenants/{tenant_id}/conversations/**
    - ワークスペース関連: /api/tenants/{tenant_id}/conversations/{id}/files/**
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
    RATE_LIMITED_PATTERNS = [
        re.compile(r"^/api/tenants/[^/]+/conversations(?:/[^/]+)?(?:/messages|/stream)?$"),
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
        app: ASGIApp,
        requests_per_window: int,
        window_seconds: int,
        key_prefix: str = "ratelimit:",
    ):
        self.app = app
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.enabled = settings.rate_limit_enabled

        if not self.enabled:
            logger.info("レート制限が無効化されています")

    def _get_headers_dict(self, scope: Scope) -> dict[str, str]:
        """scopeからヘッダー辞書を取得"""
        return dict(
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in scope.get("headers", [])
        )

    def _get_rate_limit_key(self, headers: dict[str, str], scope: Scope) -> str:
        """レート制限キーを取得"""
        user_id = headers.get("x-user-id")
        tenant_id = headers.get("x-tenant-id")

        if user_id and tenant_id:
            return f"{self.key_prefix}user:{tenant_id}:{user_id}"

        forwarded_for = headers.get("x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
            return f"{self.key_prefix}ip:{client_ip}"

        client = scope.get("client")
        if client:
            return f"{self.key_prefix}ip:{client[0]}"

        return f"{self.key_prefix}ip:unknown"

    def _should_apply_rate_limit(self, path: str) -> bool:
        """レート制限を適用すべきパスか判定"""
        if path in self.ALWAYS_SKIP_PATHS:
            return False
        for pattern in self.RATE_LIMITED_PATTERNS:
            if pattern.match(path):
                return True
        return False

    async def _check_rate_limit(self, key: str) -> tuple[bool, int, int]:
        """レート制限をチェック"""
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

    async def _send_json_response(
        self, send: Send, status_code: int, body: dict, extra_headers: list | None = None,
    ) -> None:
        """JSON レスポンスを直接送信"""
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body_bytes)).encode()],
        ]
        if extra_headers:
            headers.extend(extra_headers)

        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        })
        await send({
            "type": "http.response.body",
            "body": body_bytes,
        })

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # レート制限が無効の場合はスキップ
        if not self.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not self._should_apply_rate_limit(path):
            await self.app(scope, receive, send)
            return

        headers = self._get_headers_dict(scope)
        rate_limit_key = self._get_rate_limit_key(headers, scope)

        allowed, remaining, retry_after = await self._check_rate_limit(rate_limit_key)

        if not allowed:
            logger.warning(
                "レート制限超過",
                rate_limit_key=rate_limit_key,
                path=path,
                retry_after=retry_after,
            )
            await self._send_json_response(
                send, 429,
                {
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "リクエスト数が制限を超えました。しばらくしてから再試行してください。",
                        "retry_after": retry_after,
                    }
                },
                extra_headers=[
                    [b"retry-after", str(retry_after).encode()],
                    [b"x-ratelimit-limit", str(self.requests_per_window).encode()],
                    [b"x-ratelimit-remaining", b"0"],
                    [b"x-ratelimit-reset", str(int(time.time()) + retry_after).encode()],
                ],
            )
            return

        # レート制限ヘッダーを付与してレスポンスを送信
        rate_limit_headers = {
            b"x-ratelimit-limit": str(self.requests_per_window).encode(),
            b"x-ratelimit-remaining": str(remaining).encode(),
            b"x-ratelimit-reset": str(int(time.time()) + self.window_seconds).encode(),
        }

        async def send_with_rate_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing_headers = list(message.get("headers", []))
                for key, value in rate_limit_headers.items():
                    existing_headers.append([key, value])
                message = {**message, "headers": existing_headers}
            await send(message)

        await self.app(scope, receive, send_with_rate_limit_headers)
