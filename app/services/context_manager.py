"""
コンテキスト管理サービス

会話のコンテキストウィンドウ使用状況を管理する:
  - コンテキスト制限チェック
  - コンテキスト使用量の更新
  - context_status SSEイベントの構築
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.services.conversation_service import ConversationService
from app.services.event_translator import EventTranslator
from app.utils.streaming import (
    SequenceCounter,
    format_context_status_event,
    format_error_event,
)

logger = structlog.get_logger(__name__)


class ContextManager:
    """コンテキストウィンドウ管理"""

    def __init__(self, db: AsyncSession) -> None:
        self._conversation_service = ConversationService(db)

    async def check_context_limit(
        self,
        conversation_id: str,
        tenant_id: str,
        model: Model,
        seq_counter: SequenceCounter,
    ) -> dict | None:
        """コンテキスト制限チェック

        制限に達している場合はエラーイベントを返す。
        問題なければ None を返す。
        """
        conversation = await self._conversation_service.get_conversation_by_id(
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

    async def update_context_status(
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
        conversation = await self._conversation_service.get_conversation_by_id(
            conversation_id, tenant_id
        )
        accumulated_after = (
            (conversation.estimated_context_tokens or 0) + estimated
            if conversation
            else estimated
        )
        usage_percent = (
            (accumulated_after / max_context) * 100 if max_context > 0 else 0
        )
        limit_reached = usage_percent >= 95

        await self._conversation_service.update_conversation_context_status(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            estimated_context_tokens=estimated,
            context_limit_reached=limit_reached,
        )

    async def build_context_status_event(
        self,
        conversation_id: str,
        tenant_id: str,
        model: Model,
        done_data: dict,
        seq_counter: SequenceCounter,
    ) -> dict | None:
        """done前に送信するcontext_status SSEイベントを構築"""
        try:
            usage = EventTranslator.normalize_usage(done_data.get("usage", {}))
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            new_tokens = input_tokens + output_tokens

            conversation = await self._conversation_service.get_conversation_by_id(
                conversation_id, tenant_id
            )
            accumulated = (
                (conversation.estimated_context_tokens or 0) + new_tokens
                if conversation
                else new_tokens
            )
            max_context = model.context_window
            if max_context <= 0:
                return None

            usage_percent = (accumulated / max_context) * 100

            if usage_percent >= 95:
                warning_level = "blocked"
                can_continue = False
                message = (
                    "コンテキスト制限に達しました。新しいチャットを開始してください。"
                )
                recommended_action = "new_chat"
            elif usage_percent >= 85:
                warning_level = "critical"
                can_continue = True
                message = (
                    "コンテキストが残りわずかです。次の返信でエラーの可能性があります。"
                )
                recommended_action = "new_chat"
            elif usage_percent >= 70:
                warning_level = "warning"
                can_continue = True
                message = "会話が長くなっています。新しいチャットを開始することをおすすめします。"
                recommended_action = "new_chat"
            else:
                warning_level = "normal"
                can_continue = True
                message = None
                recommended_action = None

            return format_context_status_event(
                seq=seq_counter.next(),
                current_context_tokens=accumulated,
                max_context_tokens=max_context,
                usage_percent=usage_percent,
                warning_level=warning_level,
                can_continue=can_continue,
                message=message,
                recommended_action=recommended_action,
            )
        except Exception as e:
            logger.warning("context_statusイベント構築エラー", error=str(e))
            return None
