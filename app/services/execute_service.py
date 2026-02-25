"""
エージェント実行サービス（AgentCore Runtime版）

AgentCore Runtime 経由でエージェントを実行し、
SSEイベントを中継する。

フロー:
  1. コンテキスト制限チェック
  2. invocationペイロード構築
  3. AgentCore invoke_agent_runtime 呼出
  4. SSEイベントを中継しつつ、doneイベントから使用量を抽出
  5. file_manifest イベントから ConversationFile レコードを upsert
  6. DB記録（使用量、メッセージログ、タイトル生成）
"""

import asyncio
import base64
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.audit_log import (
    audit_agent_execution_completed,
    audit_agent_execution_failed,
    audit_agent_execution_started,
)
from app.models.conversation_file import ConversationFile
from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.execute import ExecuteRequest
from app.services.agentcore_client import AgentCoreClient
from app.services.context_manager import ContextManager
from app.services.conversation_service import ConversationService
from app.services.event_translator import EventTranslator
from app.services.mcp_config_builder import McpConfigBuilder
from app.services.mcp_server_service import McpServerService
from app.services.message_log_service import MessageLogService
from app.services.prompt_builder import build_system_prompt
from app.services.skill_service import SkillService
from app.services.usage_service import UsageService
from app.utils.sensitive_filter import sanitize_log_data
from app.utils.streaming import (
    SequenceCounter,
    format_done_event,
    format_error_event,
    format_progress_event,
    format_title_event,
)

logger = structlog.get_logger(__name__)


class ExecuteService:
    """エージェント実行サービス（AgentCore Runtime版）"""

    def __init__(
        self,
        db: AsyncSession,
        agentcore_client: AgentCoreClient,
    ):
        self.db = db
        self.agentcore = agentcore_client
        self._settings = get_settings()
        self.conversation_service = ConversationService(db)
        self.message_log_service = MessageLogService(db)
        self.usage_service = UsageService(db)
        self.skill_service = SkillService(db)
        self.mcp_server_service = McpServerService(db)
        self._event_translator = EventTranslator()
        self._mcp_config = McpConfigBuilder(self.mcp_server_service)
        self._context_manager = ContextManager(db)

    async def execute_streaming(
        self,
        request: ExecuteRequest,
        tenant: Tenant,
        model: Model,
    ) -> AsyncGenerator[dict, None]:
        """
        AgentCore Runtime 経由でエージェントをストリーミング実行

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

        logger.info(
            "エージェント実行開始（AgentCore Runtime）",
            tenant_id=request.tenant_id,
            conversation_id=conversation_id,
            model_id=model.model_id,
        )

        execution_success = False
        try:
            # ユーザーメッセージを保存
            await self._save_user_message(request)

            # ワークスペース準備を通知
            yield format_progress_event(
                seq=seq_counter.next(),
                progress_type="setup",
                message="ワークスペースを準備しています...",
            )

            audit_agent_execution_started(
                conversation_id=conversation_id,
                container_id="agentcore",
                tenant_id=request.tenant_id,
                model_id=model.model_id,
            )

            # エージェント起動を通知
            yield format_progress_event(
                seq=seq_counter.next(),
                progress_type="setup",
                message="エージェントを起動しています...",
            )

            # AgentCore経由でストリーム実行
            done_data = None
            assistant_events: list[dict] = []
            agentcore_metadata: dict = {}

            async for event in self._stream_from_agentcore(
                request, model, seq_counter, agentcore_metadata
            ):
                # done イベントからメタデータ（usage/cost）を抽出
                if event.get("event") == "done":
                    done_data = event.get("data", {})

                    # done前にcontext_statusイベントを送信
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
                        request, assistant_events, seq_counter
                    )
                    if title_event:
                        yield title_event

                # file_manifest イベントを処理（クライアントには送信しない）
                if event.get("event") == "file_manifest":
                    await self._handle_file_manifest(event, request)
                    continue

                # アシスタントメッセージ永続化用にイベントを蓄積
                _evt_type = event.get("event")
                if _evt_type in ("assistant", "thinking", "tool_call", "tool_result"):
                    assistant_events.append(event)

                yield event

            # done イベントが来なかった場合のフォールバック（ストリーム途中切断等）
            if done_data is None:
                logger.warning(
                    "done イベント未受信（フォールバックdoneを送信）",
                    conversation_id=conversation_id,
                )
                yield self._error_done(start_time, seq_counter)

            # 使用量をDB記録
            if done_data:
                await self._record_usage(request, model, done_data)
                usage = done_data.get("usage", {})
                audit_agent_execution_completed(
                    conversation_id=conversation_id,
                    container_id="agentcore",
                    tenant_id=request.tenant_id,
                    duration_ms=int((time.time() - start_time) * 1000),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cost_usd=str(done_data.get("cost_usd", "0")),
                )

                # session_id / agentcore_session_id をDBに保存（セッション再開用）
                new_session_id = done_data.get("session_id")
                new_agentcore_sid = agentcore_metadata.get("agentcore_session_id")
                if new_session_id or new_agentcore_sid:
                    await self.conversation_service.update_conversation(
                        conversation_id=request.conversation_id,
                        tenant_id=request.tenant_id,
                        session_id=new_session_id,
                        agentcore_session_id=new_agentcore_sid,
                    )

            # アシスタントメッセージをDBに保存
            if assistant_events:
                await self._save_assistant_message(request, assistant_events)

            execution_success = True

        except Exception as e:
            logger.error("エージェント実行エラー", error=str(e), exc_info=True)
            audit_agent_execution_failed(
                conversation_id=conversation_id,
                container_id="agentcore",
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

    async def _stream_from_agentcore(
        self,
        request: ExecuteRequest,
        model: Model,
        seq_counter: SequenceCounter,
        agentcore_metadata: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        """AgentCore Runtime からSSEストリームを受信・中継"""
        # MCP サーバー設定の構築
        mcp_server_configs = await self._mcp_config.build_mcp_server_configs(request)

        # スキルファイルをbase64ペイロードに変換
        skill_files = await self._build_skill_files_payload(request.tenant_id)

        # allowed_tools の計算
        allowed_tools = McpConfigBuilder.compute_allowed_tools(
            request, mcp_server_configs
        )

        # システムプロンプト構築
        skills_synced = bool(skill_files)
        system_prompt = build_system_prompt(request, skills_synced)

        # 会話のセッションIDを取得
        conversation = await self.conversation_service.get_conversation_by_id(
            request.conversation_id, request.tenant_id
        )
        sdk_session_id = conversation.session_id if conversation else None
        agentcore_session_id = (
            conversation.agentcore_session_id if conversation else None
        )

        # invocation ペイロード構築
        invocation_payload = {
            "user_input": request.user_input,
            "system_prompt": system_prompt,
            "model": model.bedrock_model_id,
            "session_id": sdk_session_id,
            "max_turns": None,
            "allowed_tools": allowed_tools,
            "cwd": "/workspace",
            "setting_sources": ["project"] if skills_synced else None,
            "mcp_server_configs": mcp_server_configs if mcp_server_configs else None,
            "workspace_sync": {
                "enabled": request.workspace_enabled
                and bool(self._settings.s3_bucket_name),
                "s3_bucket": self._settings.s3_bucket_name or "",
                "s3_prefix": self._settings.s3_prefix,
                "tenant_id": request.tenant_id,
                "conversation_id": request.conversation_id,
            },
            "skill_files": skill_files,
            "aws_region": self._settings.aws_region,
            "bedrock_model_id": model.bedrock_model_id,
        }

        # AgentCore invoke
        buffer = ""
        async for chunk in self.agentcore.invoke_streaming(
            payload=invocation_payload,
            session_id=agentcore_session_id,
            metadata=agentcore_metadata,
        ):
            decoded = chunk.decode("utf-8", errors="replace")
            buffer += decoded

            # SSEイベントをパース → 正規形式に変換して中継
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                raw_event = EventTranslator.parse_sse_event(event_str)
                if raw_event:
                    # file_manifest はそのまま内部イベントとして返す
                    if raw_event.get("event") == "file_manifest":
                        yield raw_event
                        continue

                    translated_events = self._event_translator.translate_event(
                        raw_event,
                        seq_counter,
                        conversation_id=request.conversation_id,
                    )
                    for evt in translated_events:
                        yield evt

    # ---- スキルファイル ----

    async def _build_skill_files_payload(
        self, tenant_id: str
    ) -> dict[str, str] | None:
        """テナントのスキルファイルをbase64エンコードしたdictを返却

        Returns:
            {relative_path: base64_content} または None
        """
        try:
            settings = get_settings()
            skills_base = Path(settings.skills_base_path)
            tenant_skills = (
                skills_base / f"tenant_{tenant_id}" / ".claude" / "skills"
            )

            if not tenant_skills.exists():
                return None

            skill_files: dict[str, str] = {}
            for skill_dir in tenant_skills.iterdir():
                if not skill_dir.is_dir():
                    continue
                for file_path in skill_dir.rglob("*"):
                    if not file_path.is_file():
                        continue
                    relative = file_path.relative_to(
                        skills_base / f"tenant_{tenant_id}"
                    )
                    data = file_path.read_bytes()
                    skill_files[str(relative)] = base64.b64encode(data).decode("ascii")

            return skill_files if skill_files else None
        except Exception as e:
            logger.error("スキルファイル構築エラー", error=str(e), tenant_id=tenant_id)
            return None

    # ---- file_manifest 処理 ----

    async def _handle_file_manifest(
        self, event: dict, request: ExecuteRequest
    ) -> None:
        """file_manifest イベントから ConversationFile レコードを upsert"""
        try:
            files = event.get("data", {}).get("files", [])
            if not files:
                return

            # 既存のファイルレコードを取得
            result = await self.db.execute(
                select(ConversationFile).where(
                    ConversationFile.conversation_id == request.conversation_id,
                    ConversationFile.status == "active",
                )
            )
            existing_files = {f.file_path: f for f in result.scalars().all()}

            for file_info in files:
                file_path = file_info.get("path", "")
                file_size = file_info.get("size", 0)

                if not file_path:
                    continue

                existing = existing_files.get(file_path)
                if existing:
                    # サイズ変更があれば更新レコードを挿入
                    if existing.file_size != file_size:
                        new_record = ConversationFile(
                            conversation_id=request.conversation_id,
                            file_path=file_path,
                            original_name=Path(file_path).name,
                            file_size=file_size,
                            version=existing.version + 1,
                            source="ai_modified",
                        )
                        self.db.add(new_record)
                else:
                    # 新規ファイル
                    new_record = ConversationFile(
                        conversation_id=request.conversation_id,
                        file_path=file_path,
                        original_name=Path(file_path).name,
                        file_size=file_size,
                        version=1,
                        source="ai_created",
                    )
                    self.db.add(new_record)

            await self.db.flush()
            logger.info(
                "file_manifest処理完了",
                conversation_id=request.conversation_id,
                file_count=len(files),
            )
        except Exception as e:
            logger.error("file_manifest処理エラー", error=str(e))

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

            # センシティブ情報をマスクしてからDB保存
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

            # Haikuでタイトル生成
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
