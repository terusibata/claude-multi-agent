"""
レート制限ミドルウェア

Redisベースのスライディングウィンドウアルゴリズムによるレート制限
AI実行系API（一般ユーザー向け）のみに適用

Pure ASGIミドルウェアとして実装（SSEストリーミング対応）
"""
import json
import re
import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.infrastructure.redis import redis_client

logger = structlog.get_logger(__name__)


class RateLimitMiddleware:
    """
    レート制限ミドルウェア（Pure ASGI実装）

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
        "/metrics",
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
        app: ASGIApp,
        requests_per_window: int,
        window_seconds: int,
        key_prefix: str = "ratelimit:",
    ):
        """
        初期化

        Args:
            app: 次のASGIアプリケーション
            requests_per_window: ウィンドウあたりの最大リクエスト数
            window_seconds: ウィンドウのサイズ（秒）
            key_prefix: Redisキーのプレフィックス
        """
        self.app = app
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.enabled = get_settings().rate_limit_enabled

        if not self.enabled:
            logger.info("レート制限が無効化されています")

    def _get_header(self, scope: Scope, name: bytes) -> str | None:
        """スコープからヘッダー値を取得"""
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == name:
                return header_value.decode("latin-1")
        return None

    def _get_rate_limit_key(self, scope: Scope) -> str:
        """
        レート制限キーを取得

        AI実行系APIはユーザー単位で制限:
        - X-User-ID + X-Tenant-ID: ユーザー単位（必須）
        - ヘッダーなし: IP単位（フォールバック）
        """
        user_id = self._get_header(scope, b"x-user-id")
        tenant_id = self._get_header(scope, b"x-tenant-id")

        # ユーザーIDとテナントIDがあればユーザー単位で制限
        if user_id and tenant_id:
            return f"{self.key_prefix}user:{tenant_id}:{user_id}"

        # フォールバック: IP単位
        forwarded_for = self._get_header(scope, b"x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
            return f"{self.key_prefix}ip:{client_ip}"

        client = scope.get("client")
        if client:
            return f"{self.key_prefix}ip:{client[0]}"

        return f"{self.key_prefix}ip:unknown"

    def _should_apply_rate_limit(self, path: str) -> bool:
        """
        レート制限を適用すべきパスか判定

        Returns:
            True: レート制限を適用
            False: レート制限をスキップ
        """
        if path in self.ALWAYS_SKIP_PATHS:
            return False

        for pattern in self.RATE_LIMITED_PATTERNS:
            if pattern.match(path):
                return True

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

    async def _send_json_response(
        self, send: Send, status: int, body: dict, headers: list[list[bytes]] | None = None
    ) -> None:
        """JSONレスポンスを送信"""
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        response_headers = [
            [b"content-type", b"application/json"],
        ]
        if headers:
            response_headers.extend(headers)
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": response_headers,
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

        # レート制限が無効の場合はスキップ
        if not self.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # レート制限を適用すべきか判定
        if not self._should_apply_rate_limit(path):
            await self.app(scope, receive, send)
            return

        # レート制限キーを取得
        rate_limit_key = self._get_rate_limit_key(scope)

        # レート制限チェック
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
                headers=[
                    [b"retry-after", str(retry_after).encode()],
                    [b"x-ratelimit-limit", str(self.requests_per_window).encode()],
                    [b"x-ratelimit-remaining", b"0"],
                    [b"x-ratelimit-reset", str(int(time.time()) + retry_after).encode()],
                ],
            )
            return

        # レート制限ヘッダーを注入するためのsendラッパー
        rate_limit_headers = {
            b"x-ratelimit-limit": str(self.requests_per_window).encode(),
            b"x-ratelimit-remaining": str(remaining).encode(),
            b"x-ratelimit-reset": str(int(time.time()) + self.window_seconds).encode(),
        }

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                for key, value in rate_limit_headers.items():
                    headers.append([key, value])
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
