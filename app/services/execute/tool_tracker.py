"""
ツール実行トラッカー
ツールの実行状態を追跡管理
"""
from datetime import datetime
from typing import Any, Optional

import structlog

from app.services.execute.context import ToolExecutionInfo

logger = structlog.get_logger(__name__)


class ToolTracker:
    """
    ツール実行トラッカー

    ツールの開始・完了・エラーを追跡し、サマリーを生成
    """

    def __init__(self):
        """初期化"""
        self._pending_tools: dict[str, ToolExecutionInfo] = {}
        self._completed_tools: list[ToolExecutionInfo] = []

    @property
    def pending_count(self) -> int:
        """実行中のツール数"""
        return len(self._pending_tools)

    @property
    def completed_count(self) -> int:
        """完了したツール数"""
        return len(self._completed_tools)

    @property
    def tools_used(self) -> list[dict[str, Any]]:
        """使用されたツールのサマリーを取得"""
        return [tool.to_dict() for tool in self._completed_tools]

    def start_tool(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ToolExecutionInfo:
        """
        ツール実行の開始を記録

        Args:
            tool_use_id: ツール使用ID
            tool_name: ツール名
            tool_input: ツール入力パラメータ

        Returns:
            ツール実行情報
        """
        tool_info = ToolExecutionInfo(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
            started_at=datetime.utcnow(),
        )
        self._pending_tools[tool_use_id] = tool_info

        logger.debug(
            "ツール実行開始",
            tool_use_id=tool_use_id,
            tool_name=tool_name,
        )

        return tool_info

    def complete_tool(
        self,
        tool_use_id: str,
        result: Any,
        is_error: bool = False,
    ) -> Optional[ToolExecutionInfo]:
        """
        ツール実行の完了を記録

        Args:
            tool_use_id: ツール使用ID
            result: 実行結果
            is_error: エラーかどうか

        Returns:
            ツール実行情報（存在しない場合はNone）
        """
        tool_info = self._pending_tools.pop(tool_use_id, None)
        if tool_info:
            tool_info.complete(result, is_error)
            self._completed_tools.append(tool_info)

            logger.debug(
                "ツール実行完了",
                tool_use_id=tool_use_id,
                tool_name=tool_info.tool_name,
                is_error=is_error,
            )

        return tool_info

    def get_tool_info(self, tool_use_id: str) -> Optional[ToolExecutionInfo]:
        """
        ツール情報を取得（pending優先）

        Args:
            tool_use_id: ツール使用ID

        Returns:
            ツール実行情報
        """
        if tool_use_id in self._pending_tools:
            return self._pending_tools[tool_use_id]

        for tool in self._completed_tools:
            if tool.tool_use_id == tool_use_id:
                return tool

        return None

    def get_summary(self) -> list[dict[str, Any]]:
        """
        完了したツールのサマリーを取得

        Returns:
            ツールサマリーのリスト
        """
        return self.tools_used

    def clear(self) -> None:
        """トラッカーをリセット"""
        self._pending_tools.clear()
        self._completed_tools.clear()

    def generate_result_summary(self, tool_result: Any) -> str:
        """
        ツール結果のサマリーを生成

        Args:
            tool_result: ツール実行結果

        Returns:
            結果サマリー文字列
        """
        if isinstance(tool_result, str):
            return tool_result[:200] + "..." if len(tool_result) > 200 else tool_result
        return "Result received"
