"""
エージェント実行サービス
Claude Agent SDKを使用したエージェント実行とストリーミング処理
"""
import time
from datetime import datetime
from decimal import Decimal
from typing import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.agent_config import AgentConfig
from app.models.model import Model
from app.schemas.execute import ExecuteRequest
from app.services.execute import (
    AWSConfig,
    ExecutionContext,
    MessageLogEntry,
    MessageProcessor,
    OptionsBuilder,
    TitleGenerator,
    ToolTracker,
)
from app.services.mcp_server_service import McpServerService
from app.services.session_service import SessionService
from app.services.skill_service import SkillService
from app.services.usage_service import UsageService
from app.services.workspace_service import WorkspaceService
from app.utils.session_lock import (
    SessionLockError,
    get_session_lock_manager,
)
from app.utils.streaming import (
    format_error_event,
    format_result_event,
    format_title_generated_event,
)

settings = get_settings()
logger = structlog.get_logger(__name__)


class ExecuteService:
    """エージェント実行サービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db
        self.session_service = SessionService(db)
        self.usage_service = UsageService(db)
        self.skill_service = SkillService(db)
        self.mcp_service = McpServerService(db)
        self.workspace_service = WorkspaceService(db)

        # オプションビルダーを初期化
        self.options_builder = OptionsBuilder(
            mcp_service=self.mcp_service,
            skill_service=self.skill_service,
            workspace_service=self.workspace_service,
        )

    async def execute_streaming(
        self,
        request: ExecuteRequest,
        agent_config: AgentConfig,
        model: Model,
        tenant_id: str,
    ) -> AsyncGenerator[dict, None]:
        """
        エージェントをストリーミング実行

        Args:
            request: 実行リクエスト
            agent_config: エージェント実行設定
            model: モデル定義
            tenant_id: テナントID

        Yields:
            SSEイベント辞書
        """
        # 実行コンテキストを作成
        context = ExecutionContext(
            request=request,
            agent_config=agent_config,
            model=model,
            tenant_id=tenant_id,
            start_time=time.time(),
        )

        # ツールトラッカーを初期化
        tool_tracker = ToolTracker()

        # セッションロックを取得
        lock_manager = get_session_lock_manager()
        try:
            await lock_manager.acquire(context.chat_session_id)
        except SessionLockError as e:
            logger.warning(
                "セッションロック取得失敗",
                chat_session_id=context.chat_session_id,
                error=str(e),
            )
            yield format_error_event(
                f"セッションは現在使用中です。しばらくしてから再試行してください。",
                "session_locked",
            )
            yield self._create_error_result(context, [str(e)])
            return

        logger.info(
            "エージェント実行開始",
            tenant_id=tenant_id,
            chat_session_id=context.chat_session_id,
            agent_config_id=request.agent_config_id,
            model_id=model.model_id,
            agent_skills=agent_config.agent_skills,
        )

        execution_success = False
        try:
            # セッション準備
            await self._prepare_session(context)

            # オプション構築
            options = await self.options_builder.build(context, request.tokens)
            logger.info("SDK options", options=options)

            # ターン番号とメッセージ順序取得
            context.turn_number = await self.session_service.get_latest_turn_number(
                context.chat_session_id
            ) + 1
            context.message_seq = await self.session_service.get_max_message_seq(
                context.chat_session_id
            )

            # SDKインポートと実行
            async for event in self._execute_with_sdk(context, options, tool_tracker):
                yield event

            execution_success = True

        except Exception as e:
            for event in self._handle_error(e, context, tool_tracker):
                yield event

        finally:
            # セッションロックを解放
            try:
                await lock_manager.release(context.chat_session_id)
            except Exception as lock_error:
                logger.error(
                    "セッションロック解放エラー",
                    error=str(lock_error),
                    chat_session_id=context.chat_session_id,
                )

            if execution_success:
                # 正常終了時のみcommit
                try:
                    await self.db.commit()
                except Exception as commit_error:
                    logger.error(
                        "コミットエラー",
                        error=str(commit_error),
                        chat_session_id=context.chat_session_id,
                    )
                    await self.db.rollback()
            else:
                # エラー発生時はrollback
                try:
                    await self.db.rollback()
                    logger.info(
                        "トランザクションをロールバック",
                        chat_session_id=context.chat_session_id,
                    )
                except Exception as rollback_error:
                    logger.error(
                        "ロールバックエラー",
                        error=str(rollback_error),
                        chat_session_id=context.chat_session_id,
                    )

    async def _prepare_session(self, context: ExecutionContext) -> None:
        """セッション準備"""
        logger.info("セッション確認中", chat_session_id=context.chat_session_id)

        existing_session = await self.session_service.get_session_by_id(
            context.chat_session_id, context.tenant_id
        )

        if not existing_session:
            logger.info("新規セッション作成中...")
            await self.session_service.create_session(
                chat_session_id=context.chat_session_id,
                tenant_id=context.tenant_id,
                user_id=context.request.executor.user_id,
                agent_config_id=context.request.agent_config_id,
                title=context.request.user_input[:100] if context.request.user_input else None,
            )
            logger.info("セッション作成完了")
        else:
            logger.info("既存セッションを使用")
            # resume_session_idが指定されていない場合、前回のセッションを自動引き継ぎ
            if not context.resume_session_id and existing_session.session_id:
                context.resume_session_id = existing_session.session_id
                logger.info(
                    "前回のセッションを自動復元",
                    session_id=existing_session.session_id,
                )

    async def _execute_with_sdk(
        self,
        context: ExecutionContext,
        options: dict,
        tool_tracker: ToolTracker,
    ) -> AsyncGenerator[dict, None]:
        """SDKを使用して実行"""
        logger.info("Claude Agent SDK インポート中...")

        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ClaudeSDKClient,
                ResultMessage,
                SystemMessage,
                TextBlock,
                ThinkingBlock,
                ToolResultBlock,
                ToolUseBlock,
                UserMessage,
            )
            logger.info("Claude Agent SDK インポート成功")
        except ImportError as e:
            yield format_error_event(
                f"Claude Agent SDKがインストールされていません: {str(e)}",
                "sdk_not_installed",
            )
            yield self._create_error_result(context, [str(e)])
            return

        # オプション構築
        try:
            sdk_options = ClaudeAgentOptions(**options)
            logger.info("ClaudeAgentOptions 構築成功")
        except Exception as e:
            logger.error("ClaudeAgentOptions 構築エラー", error=str(e), exc_info=True)
            yield format_error_event(
                f"SDK options構築エラー: {str(e)}", "options_error"
            )
            yield self._create_error_result(context, [str(e)])
            return

        # ユーザーメッセージを保存
        await self._save_user_message(context)

        # メッセージプロセッサを初期化
        message_processor = MessageProcessor(context, tool_tracker)

        # SDK実行
        logger.info(
            "ClaudeSDKClient実行開始",
            user_input=context.request.user_input[:100],
        )

        async with ClaudeSDKClient(options=sdk_options) as client:
            await client.query(context.request.user_input)

            async for message in client.receive_response():
                context.message_seq += 1
                timestamp = datetime.utcnow()

                # メッセージタイプ判定
                msg_type = message_processor.determine_message_type(message)
                logger.debug("メッセージ受信", seq=context.message_seq, type=msg_type)

                # ログエントリ作成
                log_entry = MessageLogEntry(
                    message_type=msg_type,
                    subtype=getattr(message, "subtype", None),
                    timestamp=timestamp,
                )

                # メッセージタイプ別処理
                if isinstance(message, SystemMessage):
                    async for event in self._wrap_generator(
                        message_processor.process_system_message(message, log_entry)
                    ):
                        yield event

                    # セッションID更新
                    if context.session_id and message.subtype == "init":
                        await self._update_session_id(context)

                elif isinstance(message, AssistantMessage):
                    async for event in self._wrap_generator(
                        message_processor.process_assistant_message(
                            message, log_entry,
                            TextBlock, ToolUseBlock, ThinkingBlock, ToolResultBlock,
                        )
                    ):
                        yield event

                elif isinstance(message, UserMessage):
                    async for event in self._wrap_generator(
                        message_processor.process_user_message(
                            message, log_entry, ToolResultBlock
                        )
                    ):
                        yield event

                elif isinstance(message, ResultMessage):
                    # 実行完了後のワークスペース同期処理
                    if context.enable_workspace:
                        await self._sync_workspace_after_execution(context)

                    result_events = await self._handle_result_message(
                        message, context, tool_tracker, log_entry
                    )
                    for event in result_events:
                        yield event

                # メッセージログ保存
                await self._save_message_log(context, msg_type, message, log_entry)

    async def _wrap_generator(self, gen):
        """同期ジェネレータを非同期で処理"""
        for item in gen:
            yield item

    async def _save_user_message(self, context: ExecutionContext) -> None:
        """ユーザーメッセージを保存"""
        context.message_seq += 1
        user_message_timestamp = datetime.utcnow()

        await self.session_service.save_message_log(
            chat_session_id=context.chat_session_id,
            message_seq=context.message_seq,
            message_type="user",
            message_subtype=None,
            content={
                "type": "user",
                "subtype": None,
                "timestamp": user_message_timestamp.isoformat(),
                "text": context.request.user_input,
            },
        )
        logger.info("ユーザーメッセージ保存完了", message_seq=context.message_seq)

    async def _update_session_id(self, context: ExecutionContext) -> None:
        """セッションIDを更新"""
        parent_id = (
            context.resume_session_id
            if context.fork_session
            else None
        )
        await self.session_service.update_session(
            chat_session_id=context.chat_session_id,
            tenant_id=context.tenant_id,
            session_id=context.session_id,
            parent_session_id=parent_id,
        )

    async def _handle_result_message(
        self,
        message,
        context: ExecutionContext,
        tool_tracker: ToolTracker,
        log_entry: MessageLogEntry,
    ) -> list[dict]:
        """
        結果メッセージを処理

        Returns:
            イベントのリスト（タイトル生成イベント + 結果イベント）
        """
        events = []

        subtype = message.subtype
        usage_data = message.usage

        # ログエントリに詳細を追加
        log_entry.result = message.result
        log_entry.is_error = message.is_error
        log_entry.usage = usage_data
        log_entry.total_cost_usd = message.total_cost_usd
        log_entry.num_turns = message.num_turns
        log_entry.session_id = message.session_id

        # 使用状況の取得
        input_tokens = usage_data.get("input_tokens", 0) if usage_data else 0
        output_tokens = usage_data.get("output_tokens", 0) if usage_data else 0
        cache_creation = usage_data.get("cache_creation_input_tokens", 0) if usage_data else 0
        cache_read = usage_data.get("cache_read_input_tokens", 0) if usage_data else 0
        total_cost = message.total_cost_usd or 0
        num_turns = message.num_turns
        duration_ms = int((time.time() - context.start_time) * 1000)

        # エラーチェック
        if message.is_error:
            context.errors.append(message.result or "Unknown error")

        # コスト計算
        if not total_cost:
            total_cost = float(
                context.model.calculate_cost(
                    input_tokens, output_tokens, cache_creation, cache_read
                )
            )

        # 使用状況ログを保存
        await self.usage_service.save_usage_log(
            tenant_id=context.tenant_id,
            user_id=context.request.executor.user_id,
            model_id=context.request.model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            cost_usd=Decimal(str(total_cost)),
            agent_config_id=context.request.agent_config_id,
            session_id=context.session_id,
            chat_session_id=context.chat_session_id,
        )

        # タイトル生成
        if context.turn_number == 1 and context.assistant_text and subtype == "success":
            title_event = await self._generate_and_update_title(context)
            events.append(title_event)

        # 結果イベントを追加
        result_event = format_result_event(
            subtype=subtype,
            result=context.assistant_text if subtype == "success" else None,
            errors=context.errors if context.errors else None,
            usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_tokens": cache_creation,
                "cache_read_tokens": cache_read,
                "total_tokens": input_tokens + output_tokens,
            },
            cost_usd=total_cost,
            num_turns=num_turns,
            duration_ms=duration_ms,
            session_id=context.session_id,
        )
        events.append(result_event)

        return events

    async def _generate_and_update_title(
        self,
        context: ExecutionContext,
    ) -> dict:
        """タイトル生成と更新"""
        logger.info("初回実行のためタイトル生成中...")

        aws_config = AWSConfig(context.model)
        title_generator = TitleGenerator(aws_config)

        generated_title = title_generator.generate(
            user_input=context.request.user_input,
            assistant_response=context.assistant_text,
            model_region=context.model.model_region or settings.aws_region,
        )

        await self.session_service.update_session_title(
            chat_session_id=context.chat_session_id,
            tenant_id=context.tenant_id,
            title=generated_title,
        )
        logger.info("タイトル更新完了", title=generated_title)

        return format_title_generated_event(generated_title)

    async def _save_message_log(
        self,
        context: ExecutionContext,
        msg_type: str,
        message,
        log_entry: MessageLogEntry,
    ) -> None:
        """メッセージログを保存"""
        should_save = True

        if msg_type == "unknown":
            should_save = False
            logger.info("unknownメッセージタイプをスキップ", message_seq=context.message_seq)
        elif msg_type == "system" and getattr(message, "subtype", None) == "init":
            if context.resume_session_id:
                should_save = False
                logger.info(
                    "継続実行のためsystem/initメッセージをスキップ",
                    message_seq=context.message_seq,
                )

        if should_save:
            await self.session_service.save_message_log(
                chat_session_id=context.chat_session_id,
                message_seq=context.message_seq,
                message_type=msg_type,
                message_subtype=getattr(message, "subtype", None),
                content=log_entry.to_dict(),
            )
        else:
            context.message_seq -= 1

    def _handle_error(
        self,
        error: Exception,
        context: ExecutionContext,
        tool_tracker: ToolTracker,
    ):
        """エラーハンドリング"""
        error_message = str(error)
        duration_ms = int((time.time() - context.start_time) * 1000)

        # ProcessErrorの場合は詳細情報を取得
        if hasattr(error, "exit_code") and hasattr(error, "stderr"):
            error_message = (
                f"Command failed with exit code {error.exit_code}\n"
                f"Error details: {error.stderr}"
            )
            logger.error(
                "エージェント実行エラー (ProcessError)",
                exit_code=error.exit_code,
                stderr=error.stderr,
                exc_info=True,
            )
        else:
            logger.error("エージェント実行エラー", error=error_message, exc_info=True)

        yield format_error_event(error_message, "execution_error")
        yield self._create_error_result(context, [error_message])

    def _create_error_result(
        self,
        context: ExecutionContext,
        errors: list[str],
    ) -> dict:
        """エラー結果イベントを生成"""
        duration_ms = int((time.time() - context.start_time) * 1000)

        return format_result_event(
            subtype="error_during_execution",
            result=None,
            errors=errors,
            usage={
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 0,
            },
            cost_usd=0,
            num_turns=0,
            duration_ms=duration_ms,
        )

    async def _sync_workspace_after_execution(
        self,
        context: ExecutionContext,
    ) -> None:
        """
        実行完了後のワークスペース同期処理

        1. ローカルからS3に同期
        2. AIファイルを自動登録
        3. ローカルクリーンアップ
        """
        try:
            # ローカルからS3に同期
            synced_files = await self.workspace_service.sync_from_local(
                context.tenant_id, context.chat_session_id
            )
            logger.info(
                "ローカル→S3同期完了",
                tenant_id=context.tenant_id,
                session_id=context.chat_session_id,
                synced_count=len(synced_files),
            )

            # AIファイルを自動登録（outputs/以下のファイル）
            for file_path in synced_files:
                if file_path.startswith("outputs/"):
                    await self.workspace_service.register_ai_file(
                        context.tenant_id,
                        context.chat_session_id,
                        file_path,
                        is_presented=True,
                    )
                    logger.info(
                        "AIファイル自動登録",
                        file_path=file_path,
                    )

            # ローカルクリーンアップ
            await self.workspace_service.cleanup_local(context.chat_session_id)
            logger.info(
                "ローカルクリーンアップ完了",
                session_id=context.chat_session_id,
            )

        except Exception as e:
            logger.error(
                "ワークスペース同期エラー",
                error=str(e),
                tenant_id=context.tenant_id,
                session_id=context.chat_session_id,
                exc_info=True,
            )
