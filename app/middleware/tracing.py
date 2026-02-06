"""
リクエストトレーシングミドルウェア

分散システムでのリクエスト追跡を実現

純粋なASGIミドルウェアとして実装し、SSEストリーミングとの互換性を確保
"""
import time
import uuid

import structlog
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

logger = structlog.get_logger(__name__)


class TracingMiddleware:
    """
    リクエストトレーシングミドルウェア（純粋なASGI実装）

    各リクエストに一意のIDを付与し、ログとレスポンスヘッダーで追跡可能にする
    """

    REQUEST_ID_HEADER = b"x-request-id"
    PROCESS_TIME_HEADER = b"x-process-time"

    # ログ出力をスキップするパス
    SKIP_LOG_PATHS = {
        "/health",
        "/health/live",
        "/health/ready",
    }

    def __init__(self, app: ASGIApp, log_requests: bool = True):
        self.app = app
        self.log_requests = log_requests

    def _get_headers_dict(self, scope: Scope) -> dict[str, str]:
        """scopeからヘッダー辞書を取得"""
        return dict(
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in scope.get("headers", [])
        )

    def _should_log(self, path: str) -> bool:
        """ログ出力すべきパスか判定"""
        if not self.log_requests:
            return False
        return path not in self.SKIP_LOG_PATHS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = self._get_headers_dict(scope)
        path = scope.get("path", "")

        # リクエストIDを取得または生成
        request_id = headers.get("x-request-id", str(uuid.uuid4()))

        # 処理開始時刻
        start_time = time.perf_counter()

        # structlogコンテキストにバインド
        clear_contextvars()
        bind_contextvars(
            request_id=request_id,
            method=scope.get("method", ""),
            path=path,
        )

        # ヘッダーから識別情報を取得してコンテキストに追加
        tenant_id = headers.get("x-tenant-id")
        user_id = headers.get("x-user-id")
        admin_id = headers.get("x-admin-id")

        if tenant_id:
            bind_contextvars(tenant_id=tenant_id)
        if user_id:
            bind_contextvars(user_id=user_id)
        if admin_id:
            bind_contextvars(admin_id=admin_id)

        # request.stateにrequest_idを保存するためscopeに追加
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        should_log = self._should_log(path)
        if should_log:
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"
            logger.info(
                "リクエスト受信",
                client_ip=client_ip,
                user_agent=headers.get("user-agent", "unknown"),
            )

        request_id_bytes = request_id.encode("latin-1")

        async def send_with_tracing(message: Message) -> None:
            if message["type"] == "http.response.start":
                process_time = time.perf_counter() - start_time
                existing_headers = list(message.get("headers", []))
                existing_headers.append([self.REQUEST_ID_HEADER, request_id_bytes])
                existing_headers.append(
                    [self.PROCESS_TIME_HEADER, f"{process_time:.4f}".encode("latin-1")]
                )
                message = {**message, "headers": existing_headers}

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
