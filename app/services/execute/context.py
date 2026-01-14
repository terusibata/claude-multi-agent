"""
実行コンテキスト
エージェント実行に必要なパラメータをまとめたデータクラス
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.execute import ExecuteRequest


@dataclass
class ExecutionContext:
    """
    エージェント実行のコンテキスト

    実行に必要な全パラメータを集約し、メソッド間で受け渡しを簡潔にする
    """

    # リクエスト情報
    request: ExecuteRequest
    tenant: Tenant
    model: Model

    # 会話関連
    conversation_id: str = ""
    session_id: Optional[str] = None  # Claude SDK用セッションID
    turn_number: int = 1
    message_seq: int = 0

    # SDK設定
    workspace_enabled: bool = False
    preferred_skills: list[str] = field(default_factory=list)

    # 実行時情報
    start_time: float = 0.0
    cwd: str = ""

    # 結果集約用
    assistant_text: str = ""
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        """初期化後の処理"""
        self.conversation_id = self.request.conversation_id
        self.workspace_enabled = self.request.workspace_enabled
        self.preferred_skills = self.request.preferred_skills or []

    @property
    def tenant_id(self) -> str:
        """テナントID"""
        return self.request.tenant_id

    @property
    def system_prompt(self) -> Optional[str]:
        """システムプロンプト"""
        return self.tenant.system_prompt


@dataclass
class ToolExecutionInfo:
    """
    ツール実行情報

    ツールの実行状態を追跡するためのデータクラス
    """

    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    status: str = "running"  # running / completed / error
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result_preview: str = ""
    is_error: bool = False

    def __post_init__(self):
        """初期化後の処理"""
        if self.started_at is None:
            self.started_at = datetime.utcnow()

    def complete(
        self,
        result: Any,
        is_error: bool = False,
    ) -> None:
        """
        ツール実行を完了としてマーク

        Args:
            result: ツール実行結果
            is_error: エラーかどうか
        """
        self.status = "error" if is_error else "completed"
        self.is_error = is_error
        self.completed_at = datetime.utcnow()

        if isinstance(result, str):
            self.result_preview = result[:200] + "..." if len(result) > 200 else result
        elif result:
            self.result_preview = str(result)[:200]
        else:
            self.result_preview = ""

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換"""
        return {
            "tool_use_id": self.tool_use_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "result_preview": self.result_preview,
        }


@dataclass
class MessageLogEntry:
    """
    メッセージログエントリ

    DBに保存するメッセージログの構造
    """

    message_type: str  # system / assistant / user_result / result / unknown
    subtype: Optional[str] = None
    timestamp: Optional[datetime] = None
    data: dict[str, Any] = field(default_factory=dict)
    content_blocks: list[dict[str, Any]] = field(default_factory=list)

    # Result specific
    result: Optional[str] = None
    is_error: bool = False
    usage: Optional[dict] = None
    total_cost_usd: Optional[float] = None
    num_turns: Optional[int] = None
    session_id: Optional[str] = None

    def __post_init__(self):
        """初期化後の処理"""
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換（DB保存用）"""
        entry = {
            "type": self.message_type,
            "subtype": self.subtype,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

        if self.data:
            entry["data"] = self.data

        if self.content_blocks:
            entry["content_blocks"] = self.content_blocks

        if self.result is not None:
            entry["result"] = self.result

        if self.is_error:
            entry["is_error"] = self.is_error

        if self.usage is not None:
            entry["usage"] = self.usage

        if self.total_cost_usd is not None:
            entry["total_cost_usd"] = self.total_cost_usd

        if self.num_turns is not None:
            entry["num_turns"] = self.num_turns

        if self.session_id is not None:
            entry["session_id"] = self.session_id

        return entry


@dataclass
class SDKOptions:
    """
    ClaudeAgentOptions構築用のデータクラス
    """

    system_prompt: Optional[str] = None
    model: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    mcp_servers: Optional[dict] = None
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    setting_sources: Optional[list[str]] = None
    resume: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """SDK用の辞書に変換"""
        options = {
            "system_prompt": self.system_prompt,
            "model": self.model,
            "allowed_tools": self.allowed_tools,
            "mcp_servers": self.mcp_servers,
            "cwd": self.cwd,
            "env": self.env,
        }

        if self.setting_sources:
            options["setting_sources"] = self.setting_sources

        if self.resume:
            options["resume"] = self.resume

        # Noneの値を削除
        return {k: v for k, v in options.items() if v is not None}
