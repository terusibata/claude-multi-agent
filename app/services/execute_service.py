"""
エージェント実行サービス（コンテナ隔離版）

会話ごとに隔離されたコンテナ内でClaude Agent SDKを実行し、
UDS（Docker）またはHTTP（ECS）経由でSSEイベントを中継する。

フロー:
  1. コンテキスト制限チェック / 会話ロック取得
  2. ContainerOrchestrator経由でコンテナ取得・作成
  3. S3 → コンテナへファイル同期
  4. コンテナ内workspace_agentにリクエスト送信（UDS / HTTP）
  5. SSEイベントを中継しつつ、doneイベントから使用量を抽出
  6. コンテナ → S3へファイル同期
  7. DB記録（使用量、メッセージログ、タイトル生成）
"""

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.audit_log import (
    audit_agent_execution_completed,
    audit_agent_execution_failed,
    audit_agent_execution_started,
)
from app.infrastructure.distributed_lock import (
    ConversationLockError,
    get_conversation_lock_manager,
)
from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.execute import ExecuteRequest
from app.services.container.models import ContainerInfo
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.context_manager import ContextManager
from app.services.conversation_service import ConversationService
from app.services.event_translator import EventTranslator
from app.services.mcp_config_builder import McpConfigBuilder
from app.services.mcp_server_service import McpServerService
from app.services.message_log_service import MessageLogService
from app.services.prompt_builder import build_system_prompt
from app.services.skill_service import SkillService
from app.services.usage_service import UsageService
from app.services.workspace.file_sync import WorkspaceFileSync
from app.services.workspace.s3_storage import S3StorageBackend
from app.utils.sensitive_filter import sanitize_log_data
from app.utils.streaming import (
    SequenceCounter,
    format_done_event,
    format_error_event,
    format_progress_event,
    format_title_event,
)

logger = structlog.get_logger(__name__)


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
        self.skill_service = SkillService(db)
        self.mcp_server_service = McpServerService(db)
        self._file_sync = self._create_file_sync()
        self._event_translator = EventTranslator()
        self._mcp_config = McpConfigBuilder(self.mcp_server_service, orchestrator)
        self._context_manager = ContextManager(db)

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

        # セットアップ開始を通知
        yield format_progress_event(
            seq=seq_counter.next(),
            progress_type="setup",
            message="実行を開始しています...",
        )

        # コンテキスト制限チェック
        context_error = await self._context_manager.check_context_limit(
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
            logger.warning(
                "会話ロック取得失敗", conversation_id=conversation_id, error=str(e)
            )
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

            # ワークスペース準備を通知
            yield format_progress_event(
                seq=seq_counter.next(),
                progress_type="setup",
                message="ワークスペースを準備しています...",
            )

            # コンテナ取得/作成（1回だけ実行し、以降はこのinfoを使い回す）
            container_info = await self.orchestrator.get_or_create(
                request.conversation_id
            )
            container_id = container_info.id

            audit_agent_execution_started(
                conversation_id=conversation_id,
                container_id=container_id,
                tenant_id=request.tenant_id,
                model_id=model.model_id,
            )

            # S3 → コンテナへファイル同期
            if request.workspace_enabled:
                yield format_progress_event(
                    seq=seq_counter.next(),
                    progress_type="setup",
                    message="ファイルを同期中...",
                )
                await self._sync_files_to_container(request, container_info)

            # セッションファイル復元（コンテナ破棄後の再開時にS3から復元）
            conversation = await self.conversation_service.get_conversation_by_id(
                request.conversation_id, request.tenant_id
            )
            if conversation and conversation.session_id and self._file_sync:
                try:
                    await self._file_sync.restore_session_file(
                        request.tenant_id,
                        request.conversation_id,
                        container_info.id,
                        conversation.session_id,
                    )
                except Exception as e:
                    logger.warning("セッションファイル復元エラー（続行）", error=str(e))

            # エージェント起動を通知
            yield format_progress_event(
                seq=seq_counter.next(),
                progress_type="setup",
                message="エージェントを起動しています...",
            )

            # コンテナ内エージェントにリクエスト送信・SSEストリーム中継
            done_data = None
            last_sync_time = 0.0
            last_lock_extend_time = time.time()
            background_sync_tasks: set[asyncio.Task] = set()
            external_file_paths: list[str] = []
            assistant_events: list[dict] = []

            async for event in self._stream_from_container(
                request,
                model,
                seq_counter,
                container_info,
            ):
                # done イベントからメタデータ（usage/cost）を抽出
                if event.get("event") == "done":
                    done_data = event.get("data", {})

                    # done前にcontext_statusイベントを送信（仕様準拠）
                    ctx_event = await self._context_manager.build_context_status_event(
                        request.conversation_id,
                        request.tenant_id,
                        model,
                        done_data,
                        seq_counter,
                    )
                    if ctx_event:
                        yield ctx_event

                    # done前にtitleイベントを送信（初回メッセージのみ）
                    title_event = await self._generate_title_if_needed(
                        request,
                        assistant_events,
                        seq_counter,
                    )
                    if title_event:
                        yield title_event

                # tool_call イベントから /workspace 外のファイルパスを収集
                EventTranslator.collect_external_file_path(event, external_file_paths)

                # 長時間実行時のロックTTL延長（60秒間隔）
                if lock_token and (time.time() - last_lock_extend_time) > 60:
                    try:
                        await lock_manager.extend(
                            conversation_id, lock_token, additional_ttl=600
                        )
                    except Exception as ext_err:
                        logger.warning(
                            "ロック延長失敗",
                            conversation_id=conversation_id,
                            error=str(ext_err),
                        )
                    last_lock_extend_time = time.time()

                # tool_result イベント検出時に非同期ファイル同期をトリガー
                if (
                    request.workspace_enabled
                    and self._settings.s3_bucket_name
                    and event.get("event") == "tool_result"
                    and EventTranslator.is_file_tool_result(event)
                    and (time.time() - last_sync_time) > _SYNC_DEBOUNCE_SECONDS
                ):
                    last_sync_time = time.time()
                    task = asyncio.create_task(
                        self._sync_files_from_container(request, container_info)
                    )
                    background_sync_tasks.add(task)
                    task.add_done_callback(background_sync_tasks.discard)

                # アシスタントメッセージ永続化用にイベントを蓄積
                _evt_type = event.get("event")
                if _evt_type in ("assistant", "thinking", "tool_call", "tool_result"):
                    assistant_events.append(event)

                yield event

            # ストリーム完了後、コンテナ情報を最新に更新
            # クラッシュ復旧時は orchestrator.execute() 内で新コンテナに
            # 切り替わっているため、後続処理が破棄済みコンテナを操作するのを防ぐ。
            # get_or_create ではなく get_container_info を使い、
            # 副作用（WarmPool取得やProxy起動）を発生させない。
            refreshed = await self.orchestrator.get_container_info(
                request.conversation_id
            )
            if refreshed:
                container_info = refreshed
                container_id = refreshed.id

            # バックグラウンド同期タスクの完了待ち（最大5秒）
            # タイムアウト後の未完了タスクはキャンセルしてリソースリークを防止
            if background_sync_tasks:
                _done, pending = await asyncio.wait(background_sync_tasks, timeout=5.0)
                for task in pending:
                    task.cancel()

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
                                request.tenant_id,
                                request.conversation_id,
                                container_id,
                                new_session_id,
                            )
                        except Exception as e:
                            logger.warning(
                                "セッションファイル保存エラー（続行）", error=str(e)
                            )

            # アシスタントメッセージをDBに保存（ストリーム完了後に一括）
            if assistant_events:
                await self._save_assistant_message(request, assistant_events)

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
        # MCP サーバー設定の構築（テナントDB → シリアライズ）
        mcp_server_configs = await self._mcp_config.build_mcp_server_configs(request)

        # MCPトークンのプロキシ側注入:
        # コンテナにトークンを渡さず、プロキシ側で認証ヘッダーを注入する
        container_mcp_configs = await self._mcp_config.extract_mcp_headers_to_proxy(
            mcp_server_configs, container_info.id
        )

        # スキルファイル同期
        skills_synced = await self._sync_skills_to_container(
            request.tenant_id, container_info.id
        )

        # allowed_tools の計算
        allowed_tools = McpConfigBuilder.compute_allowed_tools(request, mcp_server_configs)

        # システムプロンプト構築
        system_prompt = build_system_prompt(request, skills_synced)

        container_request = {
            "user_input": request.user_input,
            "system_prompt": system_prompt,
            "model": model.bedrock_model_id,
            "session_id": None,
            "max_turns": None,
            "allowed_tools": allowed_tools,
            "cwd": "/workspace",
            "setting_sources": ["project"] if skills_synced else None,
            "mcp_server_configs": container_mcp_configs
            if container_mcp_configs
            else None,
        }

        # 会話のセッションIDを取得
        conversation = await self.conversation_service.get_conversation_by_id(
            request.conversation_id, request.tenant_id
        )
        if conversation and conversation.session_id:
            container_request["session_id"] = conversation.session_id

        buffer = ""
        async for chunk in self.orchestrator.execute(
            request.conversation_id,
            container_request,
            container_info=container_info,
        ):
            decoded = chunk.decode("utf-8", errors="replace")
            buffer += decoded

            # SSEイベントをパース → 正規形式に変換して中継
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                raw_event = EventTranslator.parse_sse_event(event_str)
                if raw_event:
                    translated_events = self._event_translator.translate_event(
                        raw_event,
                        seq_counter,
                        conversation_id=request.conversation_id,
                    )
                    for evt in translated_events:
                        yield evt

    # ---- ファイル同期 ----

    async def _sync_skills_to_container(
        self, tenant_id: str, container_id: str
    ) -> bool:
        """テナントのスキルファイルをコンテナの /workspace/.claude/skills/ に同期

        スキルはホストファイルシステム上に保存されているため、
        S3設定の有無に関わらず exec 経由で直接コンテナに書き込む。
        """
        try:
            settings = get_settings()
            skills_base = Path(settings.skills_base_path)
            tenant_skills = skills_base / f"tenant_{tenant_id}" / ".claude" / "skills"

            if not tenant_skills.exists():
                return False

            synced = False
            for skill_dir in tenant_skills.iterdir():
                if not skill_dir.is_dir():
                    continue
                for file_path in skill_dir.rglob("*"):
                    if not file_path.is_file():
                        continue
                    relative = file_path.relative_to(
                        skills_base / f"tenant_{tenant_id}"
                    )
                    dest = f"/workspace/{relative}"
                    data = file_path.read_bytes()
                    await self._write_skill_to_container(container_id, dest, data)
                    synced = True

            return synced
        except Exception as e:
            logger.error("スキル同期エラー", error=str(e), tenant_id=tenant_id)
            return False

    async def _write_skill_to_container(
        self, container_id: str, dest_path: str, data: bytes
    ) -> None:
        """スキルファイルをコンテナに書き込む（S3設定不要）"""
        from app.services.container.file_utils import write_file_to_container

        await write_file_to_container(
            self.orchestrator.lifecycle,
            container_id,
            dest_path,
            data,
            tmp_prefix="_skill_xfer",
        )

    async def _sync_files_to_container(
        self, request: ExecuteRequest, container_info: ContainerInfo
    ) -> None:
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

    async def _sync_files_from_container(
        self, request: ExecuteRequest, container_info: ContainerInfo
    ) -> None:
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

    async def _rescue_external_files(
        self, container_id: str, external_paths: list[str]
    ) -> None:
        """
        /workspace外に書かれたファイルをコンテナ内で/workspaceにコピー

        ディレクトリ構造を保持してコピーする:
          /tmp/test_file.txt → /workspace/_external/tmp/test_file.txt
        """
        for src_path in external_paths:
            relative = src_path.lstrip("/")
            dest_path = f"/workspace/_external/{relative}"
            dest_dir = "/".join(dest_path.split("/")[:-1])
            try:
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

    # ---- DB記録 ----

    async def _save_user_message(self, request: ExecuteRequest) -> None:
        """ユーザーメッセージをDBに保存"""
        message_seq = (
            await self.message_log_service.get_max_message_seq(request.conversation_id)
            + 1
        )

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

    async def _save_assistant_message(
        self, request: ExecuteRequest, events: list[dict]
    ) -> None:
        """ストリーミングイベントをアシスタントメッセージとしてDBに一括保存"""
        try:
            message_seq = (
                await self.message_log_service.get_max_message_seq(
                    request.conversation_id
                )
                + 1
            )

            # センシティブ情報をマスクしてからDB保存（多層防御）
            sanitized_events = sanitize_log_data(events)

            content = {
                "type": "assistant",
                "subtype": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "events": sanitized_events,
            }

            await self.message_log_service.save_message_log(
                conversation_id=request.conversation_id,
                message_seq=message_seq,
                message_type="assistant",
                message_subtype=None,
                content=content,
            )
        except Exception as e:
            logger.error("アシスタントメッセージ保存エラー", error=str(e))

    async def _record_usage(
        self, request: ExecuteRequest, model: Model, done_data: dict
    ) -> None:
        """使用量をDBに記録"""
        try:
            # SDK/翻訳済みどちらの形式でも正規化して統一
            usage = EventTranslator.normalize_usage(done_data.get("usage", {}))
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
            await self._context_manager.update_context_status(
                request.conversation_id,
                request.tenant_id,
                model,
                input_tokens,
                output_tokens,
            )
        except Exception as e:
            logger.error("使用量記録エラー", error=str(e))

    async def _generate_title_if_needed(
        self,
        request: ExecuteRequest,
        assistant_events: list[dict],
        seq_counter: SequenceCounter,
    ) -> dict | None:
        """初回メッセージ時にタイトルを生成してtitleイベントを返す"""
        try:
            conversation = await self.conversation_service.get_conversation_by_id(
                request.conversation_id, request.tenant_id
            )
            if not conversation or conversation.title is not None:
                return None

            # アシスタントイベントからテキストを抽出
            assistant_text = ""
            for evt in assistant_events:
                if evt.get("event") == "assistant":
                    for block in evt.get("data", {}).get("content_blocks", []):
                        if block.get("type") == "text":
                            assistant_text += block.get("text", "")

            if not assistant_text:
                return None

            # Haikuでタイトル生成（同期メソッドをスレッドプールで実行）
            from app.services.aws_config import AWSConfig
            from app.services.bedrock_client import (
                BedrockChatClient,
                SimpleChatTitleGenerator,
            )

            aws_config = AWSConfig()
            bedrock_client = BedrockChatClient(aws_config)
            title_generator = SimpleChatTitleGenerator(bedrock_client)

            title = await asyncio.to_thread(
                title_generator.generate, request.user_input, assistant_text
            )

            # DBにタイトルを保存
            await self.conversation_service.update_conversation_title(
                request.conversation_id, request.tenant_id, title
            )

            logger.info(
                "会話タイトル生成完了",
                conversation_id=request.conversation_id,
                title=title,
            )

            return format_title_event(seq=seq_counter.next(), title=title)
        except Exception as e:
            logger.warning("タイトル生成エラー（続行）", error=str(e))
            return None

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
