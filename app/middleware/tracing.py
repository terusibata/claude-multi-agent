"""
リクエストトレーシングミドルウェア

分散システムでのリクエスト追跡を実現

Pure ASGIミドルウェアとして実装（SSEストリーミング対応）
"""
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

logger = structlog.get_logger(__name__)


class TracingMiddleware:
    """
    リクエストトレーシングミドルウェア（Pure ASGI実装）

    各リクエストに一意のIDを付与し、ログとレスポンスヘッダーで追跡可能にする
    """

    # リクエストIDヘッダー名
    REQUEST_ID_HEADER = b"x-request-id"
    # 処理時間ヘッダー名
    PROCESS_TIME_HEADER = b"x-process-time"

    # ログ出力をスキップするパス
    SKIP_LOG_PATHS = {
        "/health",
        "/health/live",
        "/health/ready",
    }

    def __init__(self, app: ASGIApp, log_requests: bool = True):
        """
        初期化

        Args:
            app: 次のASGIアプリケーション
            log_requests: リクエストログを出力するか
        """
        self.app = app
        self.log_requests = log_requests

    def _generate_request_id(self) -> str:
        """リクエストIDを生成"""
        return str(uuid.uuid4())

    def _get_header(self, scope: Scope, name: bytes) -> str | None:
        """スコープからヘッダー値を取得"""
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == name:
                return header_value.decode("latin-1")
        return None

    def _should_log(self, path: str) -> bool:
        """ログ出力すべきパスか判定"""
        if not self.log_requests:
            return False
        return path not in self.SKIP_LOG_PATHS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGIインターフェース"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        # リクエストIDを取得または生成
        request_id = self._get_header(scope, self.REQUEST_ID_HEADER) or self._generate_request_id()

        # 処理開始時刻
        start_time = time.perf_counter()

        # structlogコンテキストにバインド
        clear_contextvars()
        bind_contextvars(
            request_id=request_id,
            method=method,
            path=path,
        )

        # ヘッダーから識別情報を取得してコンテキストに追加
        tenant_id = self._get_header(scope, b"x-tenant-id")
        user_id = self._get_header(scope, b"x-user-id")
        admin_id = self._get_header(scope, b"x-admin-id")

        if tenant_id:
            bind_contextvars(tenant_id=tenant_id)
        if user_id:
            bind_contextvars(user_id=user_id)
        if admin_id:
            bind_contextvars(admin_id=admin_id)

        # リクエストをstateに保存（他のハンドラから参照可能）
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        should_log = self._should_log(path)

        if should_log:
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"
            user_agent = self._get_header(scope, b"user-agent") or "unknown"
            logger.info(
                "リクエスト受信",
                client_ip=client_ip,
                user_agent=user_agent,
            )

        async def send_with_tracing(message: Message) -> None:
            if message["type"] == "http.response.start":
                process_time = time.perf_counter() - start_time
                headers = list(message.get("headers", []))
                headers.append([self.REQUEST_ID_HEADER, request_id.encode()])
                headers.append([self.PROCESS_TIME_HEADER, f"{process_time:.4f}".encode()])
                message = {**message, "headers": headers}

                if should_log:
                    logger.info(
                        "レスポンス送信",
                        status_code=message.get("status"),
                        process_time_ms=round(process_time * 1000, 2),
                    )

            await send(message)

        try:
            await self.app(scope, receive, send_with_tracing)
        except Exception as e:
            process_time = time.perf_counter() - start_time
            logger.error(
                "リクエスト処理エラー",
                error=str(e),
                error_type=type(e).__name__,
                process_time_ms=round(process_time * 1000, 2),
                exc_info=True,
            )
            raise
        finally:
            clear_contextvars()
