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
from typing import AsyncGenerator

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
from app.services.container.models import ContainerInfo
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
    format_done_event,
    format_error_event,
    format_init_event,
    format_progress_event,
    format_thinking_event,
    format_tool_call_event,
    format_tool_result_event,
)
from app.utils.progress_messages import get_initial_message

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
            logger.warning(
                "S3バケット未設定: ワークスペースファイル同期が無効です。"
                "s3_bucket_name を設定してください。"
            )
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

            # セッションファイル復元（コンテナ破棄後の再開時にS3から復元）
            conversation = await self.conversation_service.get_conversation_by_id(
                request.conversation_id, request.tenant_id
            )
            if conversation and conversation.session_id and self._file_sync:
                try:
                    await self._file_sync.restore_session_file(
                        request.tenant_id, request.conversation_id,
                        container_info.id, conversation.session_id,
                    )
                except Exception as e:
                    logger.warning("セッションファイル復元エラー（続行）", error=str(e))

            # コンテナ内エージェントにリクエスト送信・SSEストリーム中継
            done_data = None
            last_sync_time = 0.0
            last_lock_extend_time = time.time()
            background_sync_tasks: set[asyncio.Task] = set()
            external_file_paths: list[str] = []  # /workspace外に書かれたファイルパスを収集

            async for event in self._stream_from_container(
                request, model, seq_counter, container_info,
            ):
                # done イベントからメタデータ（usage/cost）を抽出
                # SDK側の "done" イベントを _translate_event() でホスト形式に変換
                if event.get("event") == "done":
                    done_data = event.get("data", {})

                # tool_call イベントから /workspace 外のファイルパスを収集
                self._collect_external_file_path(event, external_file_paths)

                # 長時間実行時のロックTTL延長（60秒間隔）
                if lock_token and (time.time() - last_lock_extend_time) > 60:
                    try:
                        await lock_manager.extend(conversation_id, lock_token, additional_ttl=600)
                    except Exception as ext_err:
                        logger.warning("ロック延長失敗", conversation_id=conversation_id, error=str(ext_err))
                    last_lock_extend_time = time.time()

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

            # /workspace外に書かれたファイルをコンテナ内で/workspaceにコピー
            if external_file_paths:
                await self._rescue_external_files(
                    container_info.id, external_file_paths
                )

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

                # session_id をDBに保存（セッション再開用）
                new_session_id = done_data.get("session_id")
                if new_session_id:
                    await self.conversation_service.update_conversation(
                        conversation_id=request.conversation_id,
                        tenant_id=request.tenant_id,
                        session_id=new_session_id,
                    )

                    # セッションファイルをS3に保存（コンテナ破棄時の復旧用）
                    if self._file_sync:
                        try:
                            await self._file_sync.save_session_file(
                                request.tenant_id, request.conversation_id,
                                container_id, new_session_id,
                            )
                        except Exception as e:
                            logger.warning("セッションファイル保存エラー（続行）", error=str(e))

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
        container_info: ContainerInfo,
    ) -> AsyncGenerator[dict, None]:
        """コンテナ内エージェントからSSEストリームを受信・中継"""
        container_request = {
            "user_input": request.user_input,
            "system_prompt": (
                "あなたのワークスペースは /workspace です。"
                "ファイルの作成・編集は必ず /workspace ディレクトリ内で行ってください。"
                "相対パスを使用してください（例: hello.py, docs/readme.md）。"
                "/tmp や他のディレクトリへの書き込みは禁止です。"
            ),
            "model": model.bedrock_model_id,
            "session_id": None,
            "max_turns": None,
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
            request.conversation_id, container_request,
            container_info=container_info,
        ):
            decoded = chunk.decode("utf-8", errors="replace")
            buffer += decoded

            # SSEイベントをパース → 正規形式に変換して中継
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                raw_event = self._parse_sse_event(event_str)
                if raw_event:
                    translated_events = self._translate_event(
                        raw_event, seq_counter,
                        conversation_id=request.conversation_id,
                    )
                    for evt in translated_events:
                        yield evt

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

        # 累積後の値で limit_reached を正確に判定
        conversation = await self.conversation_service.get_conversation_by_id(
            conversation_id, tenant_id
        )
        accumulated_after = (
            (conversation.estimated_context_tokens or 0) + estimated
            if conversation else estimated
        )
        usage_percent = (accumulated_after / max_context) * 100 if max_context > 0 else 0
        limit_reached = usage_percent >= 95

        await self.conversation_service.update_conversation_context_status(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            estimated_context_tokens=estimated,
            context_limit_reached=limit_reached,
        )

    def _translate_event(
        self,
        raw_event: dict,
        seq_counter: SequenceCounter,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """
        SDKイベントをホスト正規形式に変換

        SDK側（workspace_agent）が送信するイベント形式:
          text_delta, thinking, tool_use, tool_result, done, system, error
        を、ホスト側の正規形式:
          init, progress, assistant, thinking, tool_call, tool_result, done, error
        に変換し、seq と timestamp を付与する。

        Returns:
            変換後イベントのリスト（1つのSDKイベントから複数のホストイベントを返す場合あり）
        """
        event_type = raw_event.get("event", "")
        data = raw_event.get("data", {})

        if event_type == "system" and data.get("subtype") == "init":
            # SDK system(init) → 仕様準拠の init イベントに変換
            init_data = data.get("data", {}) if isinstance(data.get("data"), dict) else data
            return [format_init_event(
                seq=seq_counter.next(),
                session_id=init_data.get("session_id", ""),
                tools=init_data.get("tools", []),
                model=init_data.get("model", ""),
                conversation_id=conversation_id,
            )]
        elif event_type == "text_delta":
            # progress(generating) + assistant
            return [
                format_progress_event(
                    seq=seq_counter.next(),
                    progress_type="generating",
                    message=get_initial_message("generating"),
                ),
                format_assistant_event(
                    seq=seq_counter.next(),
                    content_blocks=[{"type": "text", "text": data.get("text", "")}],
                ),
            ]
        elif event_type == "thinking":
            # progress(thinking) + thinking
            return [
                format_progress_event(
                    seq=seq_counter.next(),
                    progress_type="thinking",
                    message=get_initial_message("thinking"),
                ),
                format_thinking_event(
                    seq=seq_counter.next(),
                    content=data.get("content", ""),
                ),
            ]
        elif event_type == "tool_use":
            # progress(tool, running) + tool_call
            tool_name = data.get("tool_name", "")
            tool_use_id = data.get("tool_use_id", "")
            return [
                format_progress_event(
                    seq=seq_counter.next(),
                    progress_type="tool",
                    message=get_initial_message("tool", tool_name),
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    tool_status="running",
                ),
                format_tool_call_event(
                    seq=seq_counter.next(),
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    tool_input=data.get("input", {}),
                    summary=f"ツール実行: {tool_name}",
                ),
            ]
        elif event_type == "tool_result":
            # tool_result のみ（結果自体がステータスを示す）
            return [format_tool_result_event(
                seq=seq_counter.next(),
                tool_use_id=data.get("tool_use_id", ""),
                tool_name=data.get("tool_name", ""),
                status="error" if data.get("is_error") else "completed",
                content=data.get("content", ""),
                is_error=data.get("is_error", False),
            )]
        elif event_type == "done":
            return [format_done_event(
                seq=seq_counter.next(),
                status="error" if data.get("subtype") == "error_during_execution" else "success",
                result=data.get("result"),
                errors=None,
                usage=self._normalize_usage(data.get("usage", {})),
                cost_usd=str(data.get("cost_usd", "0")),
                turn_count=data.get("num_turns", 0),
                duration_ms=data.get("duration_ms", 0),
                session_id=data.get("session_id"),
            )]
        else:
            # error 等: seq/timestamp を付与してそのまま中継
            return [create_event(event_type, seq_counter.next(), data)]

    @staticmethod
    def _normalize_usage(raw_usage: dict) -> dict:
        """
        SDK usage フォーマットを仕様準拠のフォーマットに正規化

        SDK形式:
          input_tokens, output_tokens, cache_creation_input_tokens,
          cache_read_input_tokens, cache_creation.ephemeral_5m_input_tokens, ...
        仕様形式:
          input_tokens, output_tokens, cache_creation_5m_tokens,
          cache_creation_1h_tokens, cache_read_tokens, total_tokens
        """
        input_tokens = raw_usage.get("input_tokens", 0)
        output_tokens = raw_usage.get("output_tokens", 0)

        # キャッシュトークンの抽出（cache_creation ネストオブジェクトから取得）
        cache_creation = raw_usage.get("cache_creation", {})
        if isinstance(cache_creation, dict):
            cache_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
            cache_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
        else:
            cache_5m = 0
            cache_1h = 0

        # フォールバック: トップレベルの cache_creation_input_tokens を 5m として扱う
        if cache_5m == 0:
            cache_5m = raw_usage.get("cache_creation_input_tokens", 0)

        cache_read = raw_usage.get("cache_read_input_tokens", 0)

        total_tokens = input_tokens + output_tokens + cache_5m + cache_1h + cache_read

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_5m_tokens": cache_5m,
            "cache_creation_1h_tokens": cache_1h,
            "cache_read_tokens": cache_read,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _collect_external_file_path(event: dict, external_paths: list[str]) -> None:
        """
        tool_callイベントからファイルパスを抽出し、/workspace外のパスを収集

        AIがシステムプロンプトの指示を無視して/workspace外にファイルを作成した場合の
        安全策として、後でコンテナ内コピーにより回収できるようにする。
        """
        if event.get("event") != "tool_call":
            return
        data = event.get("data", {})
        tool_name = data.get("tool_name", "")
        if tool_name not in _FILE_TOOL_NAMES:
            return
        tool_input = data.get("input", {})
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return
        # /workspace 外の絶対パスのみ収集
        if file_path.startswith("/") and not file_path.startswith("/workspace/"):
            external_paths.append(file_path)

    async def _rescue_external_files(
        self, container_id: str, external_paths: list[str]
    ) -> None:
        """
        /workspace外に書かれたファイルをコンテナ内で/workspaceにコピー

        sync_from_container() は /workspace 以下のみスキャンするため、
        /workspace 外のファイルは検出されない。このメソッドで事前にコピーすることで
        同期対象に含まれるようにする。

        ディレクトリ構造を保持してコピーする:
          /tmp/test_file.txt → /workspace/_external/tmp/test_file.txt
          /home/user/data.csv → /workspace/_external/home/user/data.csv
        """
        for src_path in external_paths:
            # 先頭の / を除去してディレクトリ構造を保持
            # /tmp/test_file.txt → _external/tmp/test_file.txt
            relative = src_path.lstrip("/")
            dest_path = f"/workspace/_external/{relative}"
            dest_dir = "/".join(dest_path.split("/")[:-1])
            try:
                # 宛先ディレクトリを作成
                await self.orchestrator.lifecycle.exec_in_container(
                    container_id,
                    ["mkdir", "-p", dest_dir],
                )
                exit_code, _ = await self.orchestrator.lifecycle.exec_in_container(
                    container_id,
                    ["cp", "-f", src_path, dest_path],
                )
                if exit_code == 0:
                    logger.info(
                        "外部ファイルを/workspaceに回収",
                        src=src_path,
                        dest=dest_path,
                        container_id=container_id,
                    )
                else:
                    logger.warning(
                        "外部ファイル回収失敗（cp失敗）",
                        src=src_path,
                        exit_code=exit_code,
                    )
            except Exception as e:
                logger.warning(
                    "外部ファイル回収エラー",
                    src=src_path,
                    error=str(e),
                )

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
