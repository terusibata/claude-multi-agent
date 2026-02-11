"""
エージェント実行サービス（コンテナ隔離版）

会話ごとに隔離されたDockerコンテナ内でClaude Agent SDKを実行し、
Unix Socket経由でSSEイベントを中継する。

フロー:
  1. コンテキスト制限チェック / 会話ロック取得
  2. ContainerOrchestrator経由でコンテナ取得・作成
  3. S3 → コンテナへファイル同期
  4. コンテナ内workspace_agentにリクエスト送信（Unix Socket）
  5. SSEイベントを中継しつつ、doneイベントから使用量を抽出
  6. コンテナ → S3へファイル同期
  7. DB記録（使用量、メッセージログ、タイトル生成）
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.audit_log import (
    audit_agent_execution_completed,
    audit_agent_execution_failed,
    audit_agent_execution_started,
)
from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.execute import ExecuteRequest
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.workspace.file_sync import WorkspaceFileSync
from app.services.workspace.s3_storage import S3StorageBackend
from app.services.conversation_service import ConversationService
from app.services.message_log_service import MessageLogService
from app.services.usage_service import UsageService
from app.infrastructure.distributed_lock import (
    ConversationLockError,
    get_conversation_lock_manager,
)
from app.utils.streaming import (
    SequenceCounter,
    create_event,
    format_assistant_event,
    format_context_status_event,
    format_done_event,
    format_error_event,
    format_thinking_event,
    format_title_event,
    format_tool_call_event,
    format_tool_result_event,
)

logger = structlog.get_logger(__name__)


# ファイル操作ツール名のセット（tool_result同期トリガー用）
_FILE_TOOL_NAMES = frozenset({
    "write_file", "create_file", "edit_file", "replace_file",
    "Write", "Edit", "write", "create", "save_file",
})

# 定期同期のデバウンス間隔（秒）
_SYNC_DEBOUNCE_SECONDS = 10


class ExecuteService:
    """エージェント実行サービス（コンテナ隔離版）"""

    def __init__(
        self,
        db: AsyncSession,
        orchestrator: ContainerOrchestrator,
    ):
        self.db = db
        self.orchestrator = orchestrator
        self._settings = get_settings()
        self.conversation_service = ConversationService(db)
        self.message_log_service = MessageLogService(db)
        self.usage_service = UsageService(db)
        self._file_sync = self._create_file_sync()

    def _create_file_sync(self) -> WorkspaceFileSync | None:
        """ファイル同期インスタンスを生成（S3未設定時はNone）"""
        if not self._settings.s3_bucket_name:
            return None
        return WorkspaceFileSync(
            s3=S3StorageBackend(),
            lifecycle=self.orchestrator.lifecycle,
            db=self.db,
        )

    async def execute_streaming(
        self,
        request: ExecuteRequest,
        tenant: Tenant,
        model: Model,
    ) -> AsyncGenerator[dict, None]:
        """
        コンテナ隔離環境でエージェントをストリーミング実行

        Args:
            request: 実行リクエスト
            tenant: テナント
            model: モデル定義

        Yields:
            SSEイベント辞書
        """
        start_time = time.time()
        seq_counter = SequenceCounter()
        conversation_id = request.conversation_id

        # コンテキスト制限チェック
        context_error = await self._check_context_limit(
            conversation_id, request.tenant_id, model, seq_counter
        )
        if context_error:
            yield context_error
            yield self._error_done(start_time, seq_counter)
            return

        # 会話ロック取得
        lock_manager = get_conversation_lock_manager()
        lock_token = None
        try:
            lock_token = await lock_manager.acquire(conversation_id)
        except ConversationLockError as e:
            logger.warning("会話ロック取得失敗", conversation_id=conversation_id, error=str(e))
            yield format_error_event(
                seq=seq_counter.next(),
                error_type="conversation_locked",
                message="会話は現在使用中です。しばらくしてから再試行してください。",
                recoverable=True,
            )
            yield self._error_done(start_time, seq_counter)
            return

        logger.info(
            "エージェント実行開始（コンテナ隔離）",
            tenant_id=request.tenant_id,
            conversation_id=conversation_id,
            model_id=model.model_id,
        )

        execution_success = False
        container_id = ""
        try:
            # ユーザーメッセージを保存
            await self._save_user_message(request)

            # コンテナ取得/作成（1回だけ実行し、以降はこのinfoを使い回す）
            container_info = await self.orchestrator.get_or_create(request.conversation_id)
            container_id = container_info.id

            audit_agent_execution_started(
                conversation_id=conversation_id,
                container_id=container_id,
                tenant_id=request.tenant_id,
                model_id=model.model_id,
            )

            # S3 → コンテナへファイル同期
            if request.workspace_enabled:
                await self._sync_files_to_container(request, container_info)

            # コンテナ内エージェントにリクエスト送信・SSEストリーム中継
            done_data = None
            last_sync_time = 0.0
            background_sync_tasks: set[asyncio.Task] = set()

            async for event in self._stream_from_container(request, model, seq_counter):
                # done イベントからメタデータ（usage/cost）を抽出
                # SDK側の "done" イベントを _translate_event() でホスト形式に変換
                if event.get("event") == "done":
                    done_data = event.get("data", {})

                # tool_result イベント検出時に非同期ファイル同期をトリガー
                if (
                    request.workspace_enabled
                    and self._settings.s3_bucket_name
                    and event.get("event") == "tool_result"
                    and self._is_file_tool_result(event)
                    and (time.time() - last_sync_time) > _SYNC_DEBOUNCE_SECONDS
                ):
                    last_sync_time = time.time()
                    task = asyncio.create_task(
                        self._sync_files_from_container(request, container_info)
                    )
                    background_sync_tasks.add(task)
                    task.add_done_callback(background_sync_tasks.discard)

                yield event

            # バックグラウンド同期タスクの完了待ち（最大5秒）
            if background_sync_tasks:
                await asyncio.wait(background_sync_tasks, timeout=5.0)

            # コンテナ → S3へファイル同期
            if request.workspace_enabled:
                await self._sync_files_from_container(request, container_info)

            # 使用量をDB記録
            if done_data:
                await self._record_usage(request, model, done_data)
                usage = done_data.get("usage", {})
                audit_agent_execution_completed(
                    conversation_id=conversation_id,
                    container_id=container_id,
                    tenant_id=request.tenant_id,
                    duration_ms=int((time.time() - start_time) * 1000),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cost_usd=str(done_data.get("cost_usd", "0")),
                )

            execution_success = True

        except Exception as e:
            logger.error("エージェント実行エラー", error=str(e), exc_info=True)
            audit_agent_execution_failed(
                conversation_id=conversation_id,
                container_id=container_id,
                tenant_id=request.tenant_id,
                error=str(e),
                error_type="execution_error",
            )
            yield format_error_event(
                seq=seq_counter.next(),
                error_type="execution_error",
                message=str(e),
                recoverable=False,
            )
            yield self._error_done(start_time, seq_counter)

        finally:
            if lock_token:
                try:
                    await lock_manager.release(conversation_id, lock_token)
                except Exception as e:
                    logger.error("会話ロック解放エラー", error=str(e))

            if execution_success:
                try:
                    await self.db.commit()
                except Exception as e:
                    logger.error("コミットエラー", error=str(e))
                    await self.db.rollback()
            else:
                try:
                    await self.db.rollback()
                except Exception:
                    logger.warning("ロールバック失敗", exc_info=True)

    async def _stream_from_container(
        self,
        request: ExecuteRequest,
        model: Model,
        seq_counter: SequenceCounter,
    ) -> AsyncGenerator[dict, None]:
        """コンテナ内エージェントからSSEストリームを受信・中継"""
        container_request = {
            "user_input": request.user_input,
            "system_prompt": "",
            "model": model.bedrock_model_id,
            "session_id": None,
            "max_turns": None,
            "mcp_servers": [],
            "allowed_tools": [],
            "cwd": "/workspace",
        }

        # 会話のセッションIDを取得
        conversation = await self.conversation_service.get_conversation_by_id(
            request.conversation_id, request.tenant_id
        )
        if conversation and conversation.session_id:
            container_request["session_id"] = conversation.session_id

        buffer = ""
        async for chunk in self.orchestrator.execute(
            request.conversation_id, container_request
        ):
            decoded = chunk.decode("utf-8", errors="replace")
            buffer += decoded

            # SSEイベントをパース → 正規形式に変換して中継
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                raw_event = self._parse_sse_event(event_str)
                if raw_event:
                    translated = self._translate_event(raw_event, seq_counter)
                    if translated is not None:
                        yield translated

    def _parse_sse_event(self, event_str: str) -> dict | None:
        """SSEイベント文字列をパース"""
        event_type = "message"
        data_str = ""

        for line in event_str.strip().split("\n"):
            if line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                data_str = line[6:]

        if not data_str:
            return None

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = {"raw": data_str}

        return {"event": event_type, "data": data}

    async def _sync_files_to_container(self, request: ExecuteRequest, container_info) -> None:
        """S3からコンテナへファイルを同期"""
        if not self._file_sync:
            logger.debug("S3未設定のためファイル同期スキップ（to_container）")
            return
        try:
            await self._file_sync.sync_to_container(
                request.tenant_id, request.conversation_id, container_info.id
            )
        except Exception as e:
            logger.error("S3→コンテナ同期エラー", error=str(e))

    async def _sync_files_from_container(self, request: ExecuteRequest, container_info) -> None:
        """コンテナからS3へファイルを同期"""
        if not self._file_sync:
            logger.debug("S3未設定のためファイル同期スキップ（from_container）")
            return
        try:
            await self._file_sync.sync_from_container(
                request.tenant_id, request.conversation_id, container_info.id
            )
        except Exception as e:
            logger.error("コンテナ→S3同期エラー", error=str(e))

    async def _save_user_message(self, request: ExecuteRequest) -> None:
        """ユーザーメッセージをDBに保存"""
        message_seq = await self.message_log_service.get_max_message_seq(
            request.conversation_id
        ) + 1

        content = {
            "type": "user",
            "subtype": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": request.user_input,
        }

        await self.message_log_service.save_message_log(
            conversation_id=request.conversation_id,
            message_seq=message_seq,
            message_type="user",
            message_subtype=None,
            content=content,
        )

    async def _record_usage(
        self, request: ExecuteRequest, model: Model, done_data: dict
    ) -> None:
        """使用量をDBに記録"""
        try:
            # SDK ResultMessage 形式またはフォールバック
            usage = done_data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_5m = usage.get("cache_creation_5m_tokens", 0)
            cache_1h = usage.get("cache_creation_1h_tokens", 0)
            cache_read = usage.get("cache_read_tokens", 0)

            cost = model.calculate_cost(
                input_tokens, output_tokens, cache_5m, cache_1h, cache_read
            )

            await self.usage_service.save_usage_log(
                tenant_id=request.tenant_id,
                user_id=request.executor.user_id,
                model_id=request.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_5m_tokens=cache_5m,
                cache_creation_1h_tokens=cache_1h,
                cache_read_tokens=cache_read,
                cost_usd=cost,
                conversation_id=request.conversation_id,
            )

            # コンテキスト状況を更新
            await self._update_context_status(
                request.conversation_id, request.tenant_id,
                model, input_tokens, output_tokens,
            )
        except Exception as e:
            logger.error("使用量記録エラー", error=str(e))

    async def _check_context_limit(
        self,
        conversation_id: str,
        tenant_id: str,
        model: Model,
        seq_counter: SequenceCounter,
    ) -> dict | None:
        """コンテキスト制限チェック"""
        conversation = await self.conversation_service.get_conversation_by_id(
            conversation_id, tenant_id
        )
        if not conversation:
            return None

        if conversation.context_limit_reached:
            return format_error_event(
                seq=seq_counter.next(),
                error_type="context_limit_exceeded",
                message="この会話はコンテキスト制限に達しています。新しいチャットを開始してください。",
                recoverable=False,
            )

        max_context = model.context_window
        if max_context > 0 and conversation.estimated_context_tokens > 0:
            usage_percent = (conversation.estimated_context_tokens / max_context) * 100
            if usage_percent >= 95:
                return format_error_event(
                    seq=seq_counter.next(),
                    error_type="context_limit_exceeded",
                    message=f"コンテキスト使用率が{usage_percent:.1f}%に達しています。新しいチャットを開始してください。",
                    recoverable=False,
                )

        return None

    async def _update_context_status(
        self,
        conversation_id: str,
        tenant_id: str,
        model: Model,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """コンテキスト状況を更新"""
        estimated = input_tokens + output_tokens
        max_context = model.context_window
        usage_percent = (estimated / max_context) * 100 if max_context > 0 else 0
        limit_reached = usage_percent >= 95

        await self.conversation_service.update_conversation_context_status(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            estimated_context_tokens=estimated,
            context_limit_reached=limit_reached,
        )

    def _translate_event(self, raw_event: dict, seq_counter: SequenceCounter) -> dict:
        """
        SDKイベントをホスト正規形式に変換

        SDK側（workspace_agent）が送信するイベント形式:
          text_delta, thinking, tool_use, tool_result, done, system, error
        を、ホスト側の正規形式:
          assistant, thinking, tool_call, tool_result, done, system, error
        に変換し、seq と timestamp を付与する。
        """
        event_type = raw_event.get("event", "")
        data = raw_event.get("data", {})

        if event_type == "text_delta":
            return format_assistant_event(
                seq=seq_counter.next(),
                content_blocks=[{"type": "text", "text": data.get("text", "")}],
            )
        elif event_type == "thinking":
            return format_thinking_event(
                seq=seq_counter.next(),
                content=data.get("content", ""),
            )
        elif event_type == "tool_use":
            return format_tool_call_event(
                seq=seq_counter.next(),
                tool_use_id=data.get("tool_use_id", ""),
                tool_name=data.get("tool_name", ""),
                tool_input=data.get("input", {}),
                summary=f"ツール実行: {data.get('tool_name', '')}",
            )
        elif event_type == "tool_result":
            return format_tool_result_event(
                seq=seq_counter.next(),
                tool_use_id=data.get("tool_use_id", ""),
                tool_name=data.get("tool_name", ""),
                status="error" if data.get("is_error") else "completed",
                content=data.get("content", ""),
                is_error=data.get("is_error", False),
            )
        elif event_type == "done":
            return format_done_event(
                seq=seq_counter.next(),
                status="error" if data.get("subtype") == "error_during_execution" else "success",
                result=data.get("result"),
                errors=None,
                usage=data.get("usage", {}),
                cost_usd=data.get("cost_usd", "0"),
                turn_count=data.get("num_turns", 0),
                duration_ms=data.get("duration_ms", 0),
                session_id=data.get("session_id"),
            )
        else:
            # system, error 等: seq/timestamp を付与してそのまま中継
            return create_event(event_type, seq_counter.next(), data)

    @staticmethod
    def _is_file_tool_result(event: dict) -> bool:
        """tool_resultイベントがファイル操作ツールの結果かどうかを判定"""
        data = event.get("data", {})
        tool_name = data.get("tool_name", "")
        return tool_name in _FILE_TOOL_NAMES

    def _error_done(self, start_time: float, seq_counter: SequenceCounter) -> dict:
        """エラー時のdoneイベントを生成"""
        return format_done_event(
            seq=seq_counter.next(),
            status="error",
            result=None,
            errors=["エージェント実行に失敗しました"],
            usage={
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_5m_tokens": 0,
                "cache_creation_1h_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 0,
            },
            cost_usd="0",
            turn_count=0,
            duration_ms=int((time.time() - start_time) * 1000),
        )
