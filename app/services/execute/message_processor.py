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
    format_text_delta_event,
    format_tool_start_event,
    format_tool_complete_event,
    format_thinking_event,
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
        subtype = message.subtype
        data = message.data

        log_entry.data = data

        if subtype == "init":
            session_id = data.get("session_id")
            tools = data.get("tools", [])
            model_name = data.get("model", self.context.model.display_name)

            # セッションID更新
            if session_id:
                self.context.session_id = session_id

            # エージェント設定情報をログエントリに追加
            log_entry.data["agent_config"] = {
                "agent_config_id": self.context.agent_config.agent_config_id,
                "name": self.context.agent_config.name,
                "system_prompt": self.context.agent_config.system_prompt,
                "allowed_tools": self.context.agent_config.allowed_tools,
                "permission_mode": self.context.agent_config.permission_mode,
                "mcp_servers": self.context.agent_config.mcp_servers,
                "agent_skills": self.context.agent_config.agent_skills,
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
        content_blocks = message.content
        log_entry.content_blocks = []

        for content in content_blocks:
            # テキストブロック
            if isinstance(content, text_block_class):
                text = content.text
                self.context.assistant_text += text
                log_entry.content_blocks.append({"type": "text", "text": text})
                yield format_text_delta_event(text)

            # ツール使用ブロック
            elif isinstance(content, tool_use_block_class):
                yield from self._process_tool_use(content, log_entry)

            # 思考ブロック
            elif isinstance(content, thinking_block_class):
                thinking_text = content.text
                log_entry.content_blocks.append({
                    "type": "thinking",
                    "text": thinking_text,
                })
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
        tool_id = content.id
        tool_name = content.name
        tool_input = content.input

        # ツールトラッカーに登録
        self.tool_tracker.start_tool(tool_id, tool_name, tool_input)

        log_entry.content_blocks.append({
            "type": "tool_use",
            "id": tool_id,
            "name": tool_name,
            "input": tool_input,
        })

        summary = generate_tool_summary(tool_name, tool_input)
        yield format_tool_start_event(
            tool_id, tool_name, summary, tool_input=tool_input
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
        tool_use_id = content.tool_use_id
        tool_result = content.content
        is_error = getattr(content, "is_error", False) or False

        # ツールトラッカーで完了処理
        tool_info = self.tool_tracker.complete_tool(tool_use_id, tool_result, is_error)

        tool_name = tool_info.tool_name if tool_info else "unknown"

        # ログエントリに追加
        log_entry.content_blocks.append({
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "content": tool_result if isinstance(tool_result, str) else str(tool_result)[:500],
            "is_error": is_error,
        })

        # 結果サマリー生成
        result_summary = self.tool_tracker.generate_result_summary(tool_result)

        # tool_completeイベントを送信
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
