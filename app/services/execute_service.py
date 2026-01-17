"""
エージェント実行サービス
Claude Agent SDKを使用したエージェント実行とストリーミング処理
"""
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.execute import ExecuteRequest
from app.services.execute import (
    AWSConfig,
    ExecutionContext,
    MessageLogEntry,
    MessageProcessor,
    OptionsBuilder,
    SubagentModelMapping,
    TitleGenerator,
    ToolTracker,
)
from app.services.model_service import ModelService
from app.services.mcp_server_service import McpServerService
from app.services.conversation_service import ConversationService
from app.services.skill_service import SkillService
from app.services.usage_service import UsageService
from app.services.workspace_service import WorkspaceService
from app.utils.log_sanitizer import sanitize_sdk_options
from app.utils.conversation_lock import (
    ConversationLockError,
    get_conversation_lock_manager,
)
from app.utils.streaming import (
    format_error_event,
    format_result_event,
    format_title_generated_event,
    format_turn_progress_event,
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
        self.conversation_service = ConversationService(db)
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
        tenant: Tenant,
        model: Model,
    ) -> AsyncGenerator[dict, None]:
        """
        エージェントをストリーミング実行

        Args:
            request: 実行リクエスト
            tenant: テナント
            model: モデル定義

        Yields:
            SSEイベント辞書
        """
        # 実行コンテキストを作成
        context = ExecutionContext(
            request=request,
            tenant=tenant,
            model=model,
            start_time=time.time(),
        )

        # ツールトラッカーを初期化
        tool_tracker = ToolTracker()

        # 会話ロックを取得
        lock_manager = get_conversation_lock_manager()
        try:
            await lock_manager.acquire(context.conversation_id)
        except ConversationLockError as e:
            logger.warning(
                "会話ロック取得失敗",
                conversation_id=context.conversation_id,
                error=str(e),
            )
            yield format_error_event(
                f"会話は現在使用中です。しばらくしてから再試行してください。",
                "conversation_locked",
            )
            yield self._create_error_result(context, [str(e)])
            return

        logger.info(
            "エージェント実行開始",
            tenant_id=context.tenant_id,
            conversation_id=context.conversation_id,
            model_id=model.model_id,
        )

        execution_success = False
        try:
            # オプション構築
            options = await self.options_builder.build(context, request.tokens)
            logger.info("SDK options", options=sanitize_sdk_options(options))

            # ターン番号とメッセージ順序取得
            context.turn_number = await self.conversation_service.get_latest_turn_number(
                context.conversation_id
            ) + 1
            context.message_seq = await self.conversation_service.get_max_message_seq(
                context.conversation_id
            )

            # SDKインポートと実行
            async for event in self._execute_with_sdk(context, options, tool_tracker):
                yield event

            execution_success = True

        except Exception as e:
            for event in self._handle_error(e, context, tool_tracker):
                yield event

        finally:
            # 会話ロックを解放
            try:
                await lock_manager.release(context.conversation_id)
            except Exception as lock_error:
                logger.error(
                    "会話ロック解放エラー",
                    error=str(lock_error),
                    conversation_id=context.conversation_id,
                )

            if execution_success:
                # 正常終了時のみcommit
                try:
                    await self.db.commit()
                except Exception as commit_error:
                    logger.error(
                        "コミットエラー",
                        error=str(commit_error),
                        conversation_id=context.conversation_id,
                    )
                    await self.db.rollback()
            else:
                # エラー発生時はrollback
                try:
                    await self.db.rollback()
                    logger.info(
                        "トランザクションをロールバック",
                        conversation_id=context.conversation_id,
                    )
                except Exception as rollback_error:
                    logger.error(
                        "ロールバックエラー",
                        error=str(rollback_error),
                        conversation_id=context.conversation_id,
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

        # サブエージェント用モデルのバリデーション
        validation_error = await self._validate_subagent_models()
        if validation_error:
            yield format_error_event(validation_error, "model_validation_error")
            yield self._create_error_result(context, [validation_error])
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
        message_processor = MessageProcessor(context, tool_tracker, settings)

        # SDK実行
        logger.info(
            "ClaudeSDKClient実行開始",
            user_input=context.request.user_input[:100],
        )

        # ターン番号追跡（SDKのターン）
        sdk_turn_number = 0
        max_turns = options.get("max_turns")

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
                    # ターン進捗イベントを送信
                    sdk_turn_number += 1
                    yield format_turn_progress_event(
                        current_turn=sdk_turn_number,
                        max_turns=max_turns,
                    )

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
                    if context.workspace_enabled:
                        await self._sync_workspace_after_execution(context)

                    result_events = await self._handle_result_message(
                        message, context, tool_tracker, log_entry
                    )
                    for event in result_events:
                        yield event

                # メッセージログ保存（DB & コンテキスト）
                saved = await self._save_message_log(context, msg_type, message, log_entry)
                if saved:
                    context.message_logs.append(log_entry.to_dict())

    async def _wrap_generator(self, gen):
        """同期ジェネレータを非同期で処理"""
        for item in gen:
            yield item

    async def _save_user_message(self, context: ExecutionContext) -> None:
        """ユーザーメッセージを保存"""
        context.message_seq += 1
        user_message_timestamp = datetime.utcnow()

        user_message_content = {
            "type": "user",
            "subtype": None,
            "timestamp": user_message_timestamp.isoformat(),
            "text": context.request.user_input,
        }

        await self.conversation_service.save_message_log(
            conversation_id=context.conversation_id,
            message_seq=context.message_seq,
            message_type="user",
            message_subtype=None,
            content=user_message_content,
        )

        # message_logsにも追加（result用）
        context.message_logs.append(user_message_content)

        logger.info("ユーザーメッセージ保存完了", message_seq=context.message_seq)

    async def _update_session_id(self, context: ExecutionContext) -> None:
        """セッションIDを更新"""
        await self.conversation_service.update_conversation(
            conversation_id=context.conversation_id,
            tenant_id=context.tenant_id,
            session_id=context.session_id,
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

        # モデル別使用量を取得
        # SDKからmodel_usageが取得できる場合はそれを使用
        # 取得できない場合（Python SDKなど）はサブエージェントの追跡データから構築
        model_usage_raw = getattr(message, "model_usage", None) or getattr(message, "modelUsage", None)
        model_usage = self._normalize_model_usage(model_usage_raw)

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

        # キャッシュトークン取得（新旧両方の形式に対応）
        cache_creation = self._get_cache_creation_tokens(usage_data) if usage_data else 0
        cache_read = usage_data.get("cache_read_input_tokens", 0) if usage_data else 0
        total_cost = message.total_cost_usd or 0
        num_turns = message.num_turns
        duration_ms = int((time.time() - context.start_time) * 1000)

        # エラーチェック
        if message.is_error:
            context.errors.append(message.result or "Unknown error")

        # モデル別使用量を構築（SDKからmodel_usageが取得できない場合）
        # 注: SDKのtotal_cost_usdは使用せず、DBの価格設定から計算する
        if not model_usage:
            model_usage = await self._build_model_usage(
                context=context,
                tool_tracker=tool_tracker,
                main_input_tokens=input_tokens,
                main_output_tokens=output_tokens,
                main_cache_creation=cache_creation,
                main_cache_read=cache_read,
            )

        # コスト計算（DBの価格設定から計算、SDKのtotal_cost_usdは使用しない）
        # メインエージェントのみのコスト（全体からサブエージェント分は引かない）
        total_cost_decimal = context.model.calculate_cost(
            input_tokens, output_tokens, cache_creation, cache_read
        )

        # 使用状況ログを保存（Decimalで保存）
        await self.usage_service.save_usage_log(
            tenant_id=context.tenant_id,
            user_id=context.request.executor.user_id,
            model_id=context.request.model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            cost_usd=total_cost_decimal,
            session_id=context.session_id,
            conversation_id=context.conversation_id,
        )

        # タイトル生成
        if context.turn_number == 1 and context.assistant_text and subtype == "success":
            title_event = await self._generate_and_update_title(context)
            events.append(title_event)

        # 使用量オブジェクトを構築（ephemeral cache内訳を含む）
        usage_obj = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation,
            "cache_read_tokens": cache_read,
            "total_tokens": input_tokens + output_tokens,
        }

        # ephemeral cacheの内訳があれば追加
        if usage_data:
            cache_creation_detail = usage_data.get("cache_creation")
            if isinstance(cache_creation_detail, dict):
                usage_obj["cache_creation"] = {
                    "ephemeral_1h_input_tokens": cache_creation_detail.get("ephemeral_1h_input_tokens", 0) or 0,
                    "ephemeral_5m_input_tokens": cache_creation_detail.get("ephemeral_5m_input_tokens", 0) or 0,
                }

        # 結果イベントを追加（コストはフォーマット済み文字列で渡す）
        total_cost_formatted = self._format_cost_for_json(total_cost_decimal)
        result_event = format_result_event(
            subtype=subtype,
            result=context.assistant_text if subtype == "success" else None,
            errors=context.errors if context.errors else None,
            usage=usage_obj,
            cost_usd=total_cost_formatted,
            num_turns=num_turns,
            duration_ms=duration_ms,
            session_id=context.session_id,
            messages=context.message_logs,
            model_usage=model_usage,
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

        await self.conversation_service.update_conversation_title(
            conversation_id=context.conversation_id,
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
    ) -> bool:
        """
        メッセージログを保存

        Returns:
            保存された場合はTrue、スキップされた場合はFalse
        """
        should_save = True

        if msg_type == "unknown":
            should_save = False
            logger.info("unknownメッセージタイプをスキップ", message_seq=context.message_seq)
        elif msg_type == "system" and getattr(message, "subtype", None) == "init":
            # 継続実行の場合はsystem/initをスキップ
            existing = await self.conversation_service.get_conversation_by_id(
                context.conversation_id, context.tenant_id
            )
            if existing and existing.session_id:
                should_save = False
                logger.info(
                    "継続実行のためsystem/initメッセージをスキップ",
                    message_seq=context.message_seq,
                )

        if should_save:
            await self.conversation_service.save_message_log(
                conversation_id=context.conversation_id,
                message_seq=context.message_seq,
                message_type=msg_type,
                message_subtype=getattr(message, "subtype", None),
                content=log_entry.to_dict(),
            )
            return True
        else:
            context.message_seq -= 1
            return False

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

    def _get_cache_creation_tokens(self, usage_data: dict) -> int:
        """
        キャッシュ作成トークン数を取得

        新旧両方のSDK形式に対応:
        - 旧形式: cache_creation_input_tokens (int)
        - 新形式: cache_creation.ephemeral_1h_input_tokens + ephemeral_5m_input_tokens

        Args:
            usage_data: SDKからの使用量データ

        Returns:
            キャッシュ作成トークン数の合計
        """
        # 旧形式をチェック
        if "cache_creation_input_tokens" in usage_data:
            return usage_data.get("cache_creation_input_tokens", 0) or 0

        # 新形式をチェック
        cache_creation = usage_data.get("cache_creation")
        if isinstance(cache_creation, dict):
            ephemeral_1h = cache_creation.get("ephemeral_1h_input_tokens", 0) or 0
            ephemeral_5m = cache_creation.get("ephemeral_5m_input_tokens", 0) or 0
            return ephemeral_1h + ephemeral_5m

        return 0

    def _normalize_model_usage(
        self,
        model_usage_raw: dict | None,
    ) -> dict[str, dict[str, Any]] | None:
        """
        モデル使用量を正規化

        SDKからのmodel_usageフィールドを正規化された形式に変換

        Args:
            model_usage_raw: SDKからの生のmodel_usage

        Returns:
            正規化されたmodel_usage（なければNone）
        """
        if not model_usage_raw:
            return None

        # SDKの形式に応じて正規化
        # 期待される形式:
        # {
        #   "claude-3-5-sonnet-20241022": {
        #     "input_tokens": 1000,
        #     "output_tokens": 500,
        #     "cache_creation_input_tokens": 0,
        #     "cache_read_input_tokens": 0
        #   },
        #   "claude-3-5-haiku-20241022": {...}
        # }
        normalized = {}
        for model_id, usage in model_usage_raw.items():
            if isinstance(usage, dict):
                normalized[model_id] = {
                    "input_tokens": usage.get("input_tokens", 0) or usage.get("inputTokens", 0),
                    "output_tokens": usage.get("output_tokens", 0) or usage.get("outputTokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or usage.get("cacheCreationInputTokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or usage.get("cacheReadInputTokens", 0),
                }
        return normalized if normalized else None

    def _calculate_total_cost_from_model_usage(
        self,
        model_usage: dict[str, dict[str, Any]],
    ) -> Decimal:
        """
        モデル別使用量から合計コストを計算

        各モデルの使用量に含まれるcost_usd（Decimal）を集計する

        Args:
            model_usage: モデル別使用量（各エントリにcost_usdが含まれる）

        Returns:
            合計コスト（USD）- Decimal型
        """
        total_cost = Decimal("0")

        for model_id, usage in model_usage.items():
            cost = usage.get("cost_usd", Decimal("0"))
            if isinstance(cost, Decimal):
                total_cost += cost
            else:
                # float/intの場合はDecimalに変換
                total_cost += Decimal(str(cost))

        return total_cost

    @staticmethod
    def _format_cost_for_json(cost: Decimal) -> str:
        """
        コストを適切な精度でJSON用にフォーマット

        科学的記数法を避け、適切な小数点以下の桁数で表示する

        Args:
            cost: コスト（Decimal）

        Returns:
            フォーマットされたコスト文字列
        """
        # 小数点以下10桁まで表示（0は除去）
        # 例: Decimal("0.0000576360") -> "0.000057636"
        formatted = f"{cost:.10f}".rstrip("0").rstrip(".")
        return formatted if formatted else "0"

    async def _build_model_usage(
        self,
        context: ExecutionContext,
        tool_tracker: ToolTracker,
        main_input_tokens: int,
        main_output_tokens: int,
        main_cache_creation: int,
        main_cache_read: int,
    ) -> dict[str, dict[str, Any]]:
        """
        モデル別使用量を構築

        SDKからmodel_usageが取得できない場合（Python SDKなど）、
        サブエージェントの追跡データから構築する。
        コストはDBのmodelsテーブルの価格設定から計算する。

        Args:
            context: 実行コンテキスト
            tool_tracker: ツールトラッカー
            main_input_tokens: メイン全体の入力トークン数
            main_output_tokens: メイン全体の出力トークン数
            main_cache_creation: キャッシュ作成トークン数
            main_cache_read: キャッシュ読み込みトークン数

        Returns:
            モデルID別の使用量辞書（cost_usdはJSON用にフォーマットされた文字列）
        """
        # サブエージェントの使用量を取得（cost_usdはDecimal）
        subagent_model_usage = tool_tracker.get_aggregated_model_usage()

        # サブエージェントの合計トークン数を計算
        subagent_total_input = sum(
            u["input_tokens"] for u in subagent_model_usage.values()
        )
        subagent_total_output = sum(
            u["output_tokens"] for u in subagent_model_usage.values()
        )

        # メインエージェントの使用量（全体からサブエージェント分を引く）
        main_model_id = context.model.model_id
        main_actual_input = max(0, main_input_tokens - subagent_total_input)
        main_actual_output = max(0, main_output_tokens - subagent_total_output)

        # メインエージェントのコストをDBから計算（Decimal）
        main_cost: Decimal = context.model.calculate_cost(
            main_actual_input,
            main_actual_output,
            main_cache_creation,
            main_cache_read,
        )

        model_usage: dict[str, dict[str, Any]] = {
            main_model_id: {
                "input_tokens": main_actual_input,
                "output_tokens": main_actual_output,
                "cache_creation_input_tokens": main_cache_creation,
                "cache_read_input_tokens": main_cache_read,
                "cost_usd": main_cost,  # Decimal
            }
        }

        # サブエージェントの使用量をマージ
        # サブエージェントのcost_usdはDBから計算する必要がある
        model_service = ModelService(self.db)

        for model_id, usage in subagent_model_usage.items():
            # サブエージェントのモデル情報をDBから取得
            subagent_model = await model_service.get_by_id(model_id)

            if subagent_model:
                # DBから価格を取得してコスト計算
                subagent_cost: Decimal = subagent_model.calculate_cost(
                    usage["input_tokens"],
                    usage["output_tokens"],
                    usage.get("cache_creation_input_tokens", 0),
                    usage.get("cache_read_input_tokens", 0),
                )
            else:
                # モデルがDBにない場合は記録されたcost_usdを使用（ただし0の可能性）
                subagent_cost = usage.get("cost_usd", Decimal("0"))
                logger.warning(
                    "サブエージェントモデルがDBに存在しません",
                    model_id=model_id,
                )

            if model_id == main_model_id:
                # 同じモデルの場合はマージ
                model_usage[model_id]["input_tokens"] += usage["input_tokens"]
                model_usage[model_id]["output_tokens"] += usage["output_tokens"]
                model_usage[model_id]["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0)
                model_usage[model_id]["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0)
                # コストはDecimal同士で加算
                model_usage[model_id]["cost_usd"] += subagent_cost
            else:
                # 異なるモデルの場合は新規エントリ
                model_usage[model_id] = {
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                    "cost_usd": subagent_cost,
                }

        # JSON出力用にcost_usdをフォーマット（科学的記数法を避ける）
        formatted_usage: dict[str, dict[str, Any]] = {}
        for model_id, usage in model_usage.items():
            formatted_usage[model_id] = {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cache_creation_input_tokens": usage["cache_creation_input_tokens"],
                "cache_read_input_tokens": usage["cache_read_input_tokens"],
                "cost_usd": self._format_cost_for_json(usage["cost_usd"]),
            }

        logger.info(
            "モデル別使用量を構築",
            main_model_id=main_model_id,
            subagent_count=len(subagent_model_usage),
            model_usage=formatted_usage,
        )

        return formatted_usage

    async def _validate_subagent_models(self) -> str | None:
        """
        サブエージェント用モデルがDBに存在するか確認

        エージェント実行前にサブエージェントで使用するモデルが
        modelsテーブルに登録されているか検証する

        Returns:
            エラーメッセージ（問題がなければNone）
        """
        required_ids = SubagentModelMapping.get_required_model_ids(settings)

        model_service = ModelService(self.db)
        missing_models = []

        for model_id in required_ids:
            model = await model_service.get_by_id(model_id)
            if not model:
                missing_models.append(model_id)

        if missing_models:
            error_msg = (
                f"サブエージェント用モデルがDBに存在しません: {missing_models}. "
                "models テーブルに登録してください。"
            )
            logger.error(
                "モデルバリデーションエラー",
                missing_models=missing_models,
            )
            return error_msg

        logger.debug(
            "サブエージェント用モデルバリデーション完了",
            required_models=list(required_ids),
        )
        return None

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
        import os
        try:
            # デバッグ: ローカルディレクトリの内容を確認
            local_dir = self.workspace_service.get_workspace_local_path(context.conversation_id)
            logger.info(
                "同期前ローカルディレクトリ確認",
                local_dir=local_dir,
                cwd=context.cwd,
                exists=os.path.exists(local_dir),
                contents=os.listdir(local_dir) if os.path.exists(local_dir) else [],
            )

            # ローカルからS3に同期
            synced_files = await self.workspace_service.sync_from_local(
                context.tenant_id, context.conversation_id
            )
            logger.info(
                "ローカル→S3同期完了",
                tenant_id=context.tenant_id,
                conversation_id=context.conversation_id,
                synced_count=len(synced_files),
            )

            # AIファイルを自動登録（すべてのファイルをpresented=Trueで登録）
            for file_path in synced_files:
                await self.workspace_service.register_ai_file(
                    context.tenant_id,
                    context.conversation_id,
                    file_path,
                    is_presented=True,
                )
                logger.info(
                    "AIファイル自動登録",
                    file_path=file_path,
                )

            # ローカルクリーンアップ
            await self.workspace_service.cleanup_local(context.conversation_id)
            logger.info(
                "ローカルクリーンアップ完了",
                conversation_id=context.conversation_id,
            )

        except Exception as e:
            logger.error(
                "ワークスペース同期エラー",
                error=str(e),
                tenant_id=context.tenant_id,
                conversation_id=context.conversation_id,
                exc_info=True,
            )
