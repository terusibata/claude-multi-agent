"""
リクエストトレーシングミドルウェア

分散システムでのリクエスト追跡を実現
"""
import time
import uuid
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

logger = structlog.get_logger(__name__)


class TracingMiddleware(BaseHTTPMiddleware):
    """
    リクエストトレーシングミドルウェア

    各リクエストに一意のIDを付与し、ログとレスポンスヘッダーで追跡可能にする
    """

    # リクエストIDヘッダー名
    REQUEST_ID_HEADER = "X-Request-ID"
    # 処理時間ヘッダー名
    PROCESS_TIME_HEADER = "X-Process-Time"

    # ログ出力をスキップするパス
    SKIP_LOG_PATHS = {
        "/health",
        "/health/live",
        "/health/ready",
    }

    def __init__(self, app, log_requests: bool = True):
        """
        初期化

        Args:
            app: FastAPIアプリケーション
            log_requests: リクエストログを出力するか
        """
        super().__init__(app)
        self.log_requests = log_requests

    def _generate_request_id(self) -> str:
        """リクエストIDを生成"""
        return str(uuid.uuid4())

    def _should_log(self, path: str) -> bool:
        """ログ出力すべきパスか判定"""
        if not self.log_requests:
            return False
        return path not in self.SKIP_LOG_PATHS

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """リクエストを処理"""
        # リクエストIDを取得または生成
        request_id = request.headers.get(
            self.REQUEST_ID_HEADER,
            self._generate_request_id(),
        )

        # 処理開始時刻
        start_time = time.perf_counter()

        # structlogコンテキストにバインド
        clear_contextvars()
        bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        # ヘッダーから識別情報を取得してコンテキストに追加
        # AI実行系API用: X-Tenant-ID + X-User-ID
        tenant_id = request.headers.get("X-Tenant-ID")
        user_id = request.headers.get("X-User-ID")
        # 管理系API用: X-Admin-ID
        admin_id = request.headers.get("X-Admin-ID")

        if tenant_id:
            bind_contextvars(tenant_id=tenant_id)
        if user_id:
            bind_contextvars(user_id=user_id)
        if admin_id:
            bind_contextvars(admin_id=admin_id)

        # リクエストをstateに保存（他のハンドラから参照可能）
        request.state.request_id = request_id

        # リクエストログ
        should_log = self._should_log(request.url.path)
        if should_log:
            logger.info(
                "リクエスト受信",
                client_ip=request.client.host if request.client else "unknown",
                user_agent=request.headers.get("User-Agent", "unknown"),
            )

        try:
            # リクエストを処理
            response = await call_next(request)

            # 処理時間を計算
            process_time = time.perf_counter() - start_time

            # レスポンスヘッダーを追加
            response.headers[self.REQUEST_ID_HEADER] = request_id
            response.headers[self.PROCESS_TIME_HEADER] = f"{process_time:.4f}"

            # レスポンスログ
            if should_log:
                logger.info(
                    "レスポンス送信",
                    status_code=response.status_code,
                    process_time_ms=round(process_time * 1000, 2),
                )

            return response

        except Exception as e:
            # エラーログ
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
            # コンテキストをクリア
            clear_contextvars()
