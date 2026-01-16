"""
メッセージプロセッサ
SDKからのメッセージを処理してSSEイベントに変換
"""
from datetime import datetime
from typing import Any, Generator, Optional

import structlog

from app.services.execute.context import ExecutionContext, MessageLogEntry
from app.services.execute.tool_tracker import ToolTracker
from app.utils.streaming import (
    format_session_start_event,
    format_status_event,
    format_subagent_event,
    format_text_delta_event,
    format_thinking_event,
    format_tool_complete_event,
    format_tool_progress_event,
    format_tool_start_event,
)
from app.utils.tool_summary import generate_tool_summary

logger = structlog.get_logger(__name__)


class MessageProcessor:
    """
    メッセージプロセッサ

    SDKからのメッセージタイプを判定し、適切なSSEイベントを生成
    """

    def __init__(
        self,
        context: ExecutionContext,
        tool_tracker: ToolTracker,
    ):
        """
        初期化

        Args:
            context: 実行コンテキスト
            tool_tracker: ツールトラッカー
        """
        self.context = context
        self.tool_tracker = tool_tracker

    def process_system_message(
        self,
        message: Any,
        log_entry: MessageLogEntry,
    ) -> Generator[dict, None, None]:
        """
        システムメッセージを処理

        Args:
            message: SDKメッセージ
            log_entry: ログエントリ

        Yields:
            SSEイベント
        """
        # 属性を安全に取得（SDK変更への耐性）
        subtype = getattr(message, "subtype", None)
        data = getattr(message, "data", {}) or {}

        log_entry.data = data

        if subtype == "init":
            session_id = data.get("session_id")
            tools = data.get("tools", [])
            model_name = data.get("model", self.context.model.display_name)

            # セッションID更新
            if session_id:
                self.context.session_id = session_id

            # テナント設定情報をログエントリに追加
            # システムプロンプトは機密情報を含む可能性があるため省略
            system_prompt = self.context.system_prompt or ""
            log_entry.data["tenant_config"] = {
                "tenant_id": self.context.tenant_id,
                "system_prompt_length": len(system_prompt),
                "system_prompt_preview": system_prompt[:50] + "..." if len(system_prompt) > 50 else system_prompt,
            }
            log_entry.data["model_config"] = {
                "model_id": self.context.model.model_id,
                "display_name": self.context.model.display_name,
                "bedrock_model_id": self.context.model.bedrock_model_id,
                "model_region": self.context.model.model_region,
            }

            yield format_session_start_event(
                session_id=session_id or "",
                tools=tools,
                model=model_name,
            )

    def process_assistant_message(
        self,
        message: Any,
        log_entry: MessageLogEntry,
        text_block_class: type,
        tool_use_block_class: type,
        thinking_block_class: type,
        tool_result_block_class: type,
    ) -> Generator[dict, None, None]:
        """
        アシスタントメッセージを処理

        Args:
            message: SDKメッセージ
            log_entry: ログエントリ
            text_block_class: TextBlockクラス
            tool_use_block_class: ToolUseBlockクラス
            thinking_block_class: ThinkingBlockクラス
            tool_result_block_class: ToolResultBlockクラス

        Yields:
            SSEイベント
        """
        # 属性を安全に取得
        content_blocks = getattr(message, "content", []) or []
        log_entry.content_blocks = []

        for content in content_blocks:
            # テキストブロック
            if isinstance(content, text_block_class):
                text = getattr(content, "text", "") or ""
                self.context.assistant_text += text
                log_entry.content_blocks.append({"type": "text", "text": text})
                # ステータスイベント: テキスト生成中
                yield format_status_event("generating", "レスポンスを生成中...")
                yield format_text_delta_event(text)

            # ツール使用ブロック
            elif isinstance(content, tool_use_block_class):
                yield from self._process_tool_use(content, log_entry)

            # 思考ブロック
            elif isinstance(content, thinking_block_class):
                thinking_text = getattr(content, "text", "") or ""
                log_entry.content_blocks.append({
                    "type": "thinking",
                    "text": thinking_text,
                })
                # ステータスイベント: 思考中
                yield format_status_event("thinking", "思考中...")
                yield format_thinking_event(thinking_text)

            # ツール結果ブロック
            elif isinstance(content, tool_result_block_class):
                yield from self._process_tool_result(content, log_entry)

    def process_user_message(
        self,
        message: Any,
        log_entry: MessageLogEntry,
        tool_result_block_class: type,
    ) -> Generator[dict, None, None]:
        """
        ユーザーメッセージ（ツール結果）を処理

        Args:
            message: SDKメッセージ
            log_entry: ログエントリ
            tool_result_block_class: ToolResultBlockクラス

        Yields:
            SSEイベント
        """
        content_blocks = getattr(message, "content", [])
        log_entry.content_blocks = []

        for content in content_blocks:
            if isinstance(content, tool_result_block_class):
                yield from self._process_tool_result(content, log_entry)

    def _process_tool_use(
        self,
        content: Any,
        log_entry: MessageLogEntry,
    ) -> Generator[dict, None, None]:
        """
        ツール使用ブロックを処理

        Args:
            content: ツール使用ブロック
            log_entry: ログエントリ

        Yields:
            SSEイベント
        """
        # 属性を安全に取得（SDK変更への耐性）
        tool_id = getattr(content, "id", None) or "unknown"
        tool_name = getattr(content, "name", None) or "unknown"
        tool_input = getattr(content, "input", {}) or {}

        # ツールトラッカーに登録
        self.tool_tracker.start_tool(tool_id, tool_name, tool_input)

        summary = generate_tool_summary(tool_name, tool_input)

        log_entry.content_blocks.append({
            "type": "tool_use",
            "id": tool_id,
            "name": tool_name,
            "input": tool_input,
            "summary": summary,
        })

        # ステータスイベント: ツール実行中
        yield format_status_event("tool_execution", f"ツール実行中: {tool_name}")

        # ツール進捗イベント: pending（受付）
        yield format_tool_progress_event(
            tool_use_id=tool_id,
            tool_name=tool_name,
            status="pending",
            message=summary,
        )

        # ツール開始イベント
        yield format_tool_start_event(
            tool_id, tool_name, summary, tool_input=tool_input
        )

        # ツール進捗イベント: running（実行中）
        yield format_tool_progress_event(
            tool_use_id=tool_id,
            tool_name=tool_name,
            status="running",
            message=f"{tool_name}を実行中...",
        )

        # Taskツールの場合はサブエージェント開始イベントを送信
        if tool_name == "Task":
            subagent_type = tool_input.get("subagent_type", "unknown")
            description = tool_input.get("description", "サブエージェント実行")
            yield format_subagent_event(
                action="start",
                agent_type=subagent_type,
                description=description,
                parent_tool_use_id=tool_id,
            )

    def _process_tool_result(
        self,
        content: Any,
        log_entry: MessageLogEntry,
    ) -> Generator[dict, None, None]:
        """
        ツール結果ブロックを処理

        Args:
            content: ツール結果ブロック
            log_entry: ログエントリ

        Yields:
            SSEイベント
        """
        # 属性を安全に取得（SDK変更への耐性）
        tool_use_id = getattr(content, "tool_use_id", None) or "unknown"
        tool_result = getattr(content, "content", None)
        is_error = getattr(content, "is_error", False) or False

        # ツールトラッカーで完了処理
        tool_info = self.tool_tracker.complete_tool(tool_use_id, tool_result, is_error)

        tool_name = tool_info.tool_name if tool_info else "unknown"
        tool_input = tool_info.tool_input if tool_info else {}

        # ステータス決定
        status = "error" if is_error else "completed"

        # ログエントリに追加
        log_entry.content_blocks.append({
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "content": tool_result if isinstance(tool_result, str) else str(tool_result)[:500],
            "is_error": is_error,
            "status": status,
        })

        # 結果サマリー生成
        result_summary = self.tool_tracker.generate_result_summary(tool_result)

        # ツール進捗イベント: completed / error
        yield format_tool_progress_event(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            status=status,
            message=result_summary,
        )

        # Taskツールの場合はサブエージェント終了イベントを送信
        if tool_name == "Task":
            subagent_type = tool_input.get("subagent_type", "unknown")
            description = tool_input.get("description", "サブエージェント完了")
            yield format_subagent_event(
                action="stop",
                agent_type=subagent_type,
                description=description,
                parent_tool_use_id=tool_use_id,
                result=result_summary[:200] if result_summary else None,
            )

        # tool_resultイベントを送信
        yield format_tool_complete_event(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            status="error" if is_error else "completed",
            summary=result_summary,
            result_preview=result_summary,
            is_error=is_error,
        )

    def determine_message_type(self, message: Any) -> str:
        """
        メッセージタイプを判定

        Args:
            message: SDKメッセージ

        Returns:
            メッセージタイプ文字列
        """
        type_name = type(message).__name__

        type_mapping = {
            "SystemMessage": "system",
            "AssistantMessage": "assistant",
            "UserMessage": "user_result",
            "ResultMessage": "result",
        }

        msg_type = type_mapping.get(type_name, "unknown")

        if msg_type == "unknown":
            logger.warning(
                "未知のメッセージタイプを受信",
                message_class=type_name,
            )

        return msg_type
