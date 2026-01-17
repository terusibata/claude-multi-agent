"""
ツール実行トラッカー
ツールの実行状態を追跡管理
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import structlog

from app.services.execute.context import ToolExecutionInfo

logger = structlog.get_logger(__name__)


@dataclass
class SubagentUsageInfo:
    """サブエージェントの使用量情報"""

    tool_use_id: str
    agent_type: str
    model_alias: str | None  # "haiku", "sonnet" など
    model_id: str  # 解決された実際のモデルID
    description: str
    started_at: datetime
    completed_at: datetime | None = None

    # 使用量（Task完了時のtool_resultから取得）
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    duration_ms: int = 0


class ToolTracker:
    """
    ツール実行トラッカー

    ツールの開始・完了・エラーを追跡し、サマリーを生成
    サブエージェント（Task）の並列実行にも対応
    """

    def __init__(self):
        """初期化"""
        self._pending_tools: dict[str, ToolExecutionInfo] = {}
        self._completed_tools: list[ToolExecutionInfo] = []
        # サブエージェント管理（並列実行対応）
        # key: tool_use_id, value: サブエージェント情報
        self._active_subagents: dict[str, dict[str, Any]] = {}
        # ツールとサブエージェントの親子関係
        # key: child_tool_use_id, value: parent_tool_use_id（サブエージェントのtool_use_id）
        self._tool_parent_map: dict[str, str] = {}
        # サブエージェント使用量追跡（完了したサブエージェントの使用量）
        self._subagent_usages: list[SubagentUsageInfo] = []

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

    @property
    def is_in_subagent(self) -> bool:
        """サブエージェント実行中かどうか"""
        return len(self._active_subagents) > 0

    @property
    def active_subagent_count(self) -> int:
        """アクティブなサブエージェント数"""
        return len(self._active_subagents)

    def start_subagent(
        self,
        tool_use_id: str,
        agent_type: str,
        description: str,
        model_alias: str | None = None,
        model_id: str | None = None,
    ) -> None:
        """
        サブエージェントの開始を記録

        Args:
            tool_use_id: TaskツールのID
            agent_type: サブエージェントタイプ
            description: 説明
            model_alias: モデルエイリアス（例: "haiku", "sonnet"）
            model_id: 解決されたモデルID
        """
        self._active_subagents[tool_use_id] = {
            "agent_type": agent_type,
            "description": description,
            "started_at": datetime.utcnow(),
            "model_alias": model_alias,
            "model_id": model_id or "unknown",
        }
        logger.debug(
            "サブエージェント開始",
            tool_use_id=tool_use_id,
            agent_type=agent_type,
            model_alias=model_alias,
            model_id=model_id,
        )

    def complete_subagent(self, tool_use_id: str) -> Optional[dict[str, Any]]:
        """
        サブエージェントの完了を記録（使用量なし）

        Args:
            tool_use_id: TaskツールのID

        Returns:
            サブエージェント情報（存在しない場合はNone）
        """
        info = self._active_subagents.pop(tool_use_id, None)
        if info:
            logger.debug(
                "サブエージェント完了",
                tool_use_id=tool_use_id,
                agent_type=info["agent_type"],
            )
        return info

    def complete_subagent_with_usage(
        self,
        tool_use_id: str,
        usage: dict[str, Any] | None,
        total_cost_usd: Decimal | None,
        duration_ms: int | None,
    ) -> SubagentUsageInfo | None:
        """
        サブエージェント完了時に使用量を記録

        Task toolのtool_resultから取得したusage情報を記録する

        Args:
            tool_use_id: TaskツールのID
            usage: SDK tool_resultからの使用量データ
            total_cost_usd: コスト（USD）- DBから計算された値
            duration_ms: 実行時間（ミリ秒）

        Returns:
            サブエージェント使用量情報（サブエージェントが見つからない場合はNone）
        """
        info = self._active_subagents.pop(tool_use_id, None)
        if not info:
            logger.warning(
                "サブエージェント情報が見つかりません",
                tool_use_id=tool_use_id,
            )
            return None

        usage_info = SubagentUsageInfo(
            tool_use_id=tool_use_id,
            agent_type=info["agent_type"],
            model_alias=info.get("model_alias"),
            model_id=info.get("model_id", "unknown"),
            description=info["description"],
            started_at=info["started_at"],
            completed_at=datetime.utcnow(),
            input_tokens=usage.get("input_tokens", 0) if usage else 0,
            output_tokens=usage.get("output_tokens", 0) if usage else 0,
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) if usage else 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) if usage else 0,
            total_cost_usd=total_cost_usd or Decimal("0"),
            duration_ms=duration_ms or 0,
        )

        self._subagent_usages.append(usage_info)

        logger.info(
            "サブエージェント使用量記録",
            tool_use_id=tool_use_id,
            agent_type=info["agent_type"],
            model_id=usage_info.model_id,
            input_tokens=usage_info.input_tokens,
            output_tokens=usage_info.output_tokens,
            total_cost_usd=str(usage_info.total_cost_usd),
        )

        return usage_info

    def get_current_parent_tool_id(self) -> Optional[str]:
        """
        現在のサブエージェントの親ツールIDを取得

        並列実行の場合、最後に開始されたサブエージェントのIDを返す
        メインエージェントの場合はNoneを返す

        注意: 並列サブエージェント実行時の制限事項
        SDKからはどのサブエージェントに子ツールが属するかの情報が得られないため、
        最後に開始されたサブエージェントを親として割り当てます。
        これはベストエフォートの実装であり、並列実行時には不正確になる可能性があります。

        Returns:
            親ツールID（メインエージェントの場合はNone）
        """
        if not self._active_subagents:
            return None
        # 最後に開始されたサブエージェントを返す（ベストエフォート）
        return list(self._active_subagents.keys())[-1]

    def get_parent_tool_id_for_tool(self, tool_use_id: str) -> Optional[str]:
        """
        特定のツールの親ツールIDを取得

        Args:
            tool_use_id: ツール使用ID

        Returns:
            親ツールID（存在しない場合はNone）
        """
        return self._tool_parent_map.get(tool_use_id)

    def start_tool(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        model_alias: str | None = None,
        model_id: str | None = None,
    ) -> ToolExecutionInfo:
        """
        ツール実行の開始を記録

        Args:
            tool_use_id: ツール使用ID
            tool_name: ツール名
            tool_input: ツール入力パラメータ
            model_alias: モデルエイリアス（Task toolの場合のみ使用）
            model_id: 解決されたモデルID（Task toolの場合のみ使用）

        Returns:
            ツール実行情報
        """
        # 親ツールIDを決定（サブエージェント内の場合）
        parent_tool_id = self.get_current_parent_tool_id()

        tool_info = ToolExecutionInfo(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
            started_at=datetime.utcnow(),
            parent_tool_use_id=parent_tool_id,
        )
        self._pending_tools[tool_use_id] = tool_info

        # 親子関係を記録
        if parent_tool_id:
            self._tool_parent_map[tool_use_id] = parent_tool_id

        # Taskツールの場合はサブエージェント開始として記録
        if tool_name == "Task":
            subagent_type = tool_input.get("subagent_type", "unknown")
            description = tool_input.get("description", "")
            self.start_subagent(
                tool_use_id,
                subagent_type,
                description,
                model_alias=model_alias,
                model_id=model_id,
            )

        logger.debug(
            "ツール実行開始",
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            parent_tool_id=parent_tool_id,
            model_alias=model_alias if tool_name == "Task" else None,
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

            # Taskツールの場合はサブエージェント完了として記録
            if tool_info.tool_name == "Task":
                self.complete_subagent(tool_use_id)

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
        self._subagent_usages.clear()
        self._active_subagents.clear()
        self._tool_parent_map.clear()

    def get_subagent_usages(self) -> list[SubagentUsageInfo]:
        """
        すべてのサブエージェント使用量を取得

        Returns:
            サブエージェント使用量情報のリスト
        """
        return self._subagent_usages.copy()

    def get_aggregated_model_usage(self) -> dict[str, dict[str, Any]]:
        """
        モデルごとに使用量を集計

        サブエージェントの使用量をモデルID別に集計して返す

        Returns:
            モデルID別の使用量辞書
            {
                "us.anthropic.claude-haiku-4-5-20251001-v1:0": {
                    "input_tokens": 5000,
                    "output_tokens": 1000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cost_usd": Decimal("0.015"),
                    "subagent_count": 2,
                },
                ...
            }
        """
        aggregated: dict[str, dict[str, Any]] = {}

        for usage in self._subagent_usages:
            model_id = usage.model_id

            if model_id not in aggregated:
                aggregated[model_id] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cost_usd": Decimal("0"),
                    "subagent_count": 0,
                }

            aggregated[model_id]["input_tokens"] += usage.input_tokens
            aggregated[model_id]["output_tokens"] += usage.output_tokens
            aggregated[model_id]["cache_creation_input_tokens"] += usage.cache_creation_tokens
            aggregated[model_id]["cache_read_input_tokens"] += usage.cache_read_tokens
            aggregated[model_id]["cost_usd"] += usage.total_cost_usd
            aggregated[model_id]["subagent_count"] += 1

        return aggregated

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
