"""
ツール実行ログテーブル
エージェントが使用したツールの実行記録
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ToolExecutionLog(Base):
    """
    ツール実行ログテーブル
    エージェントが使用したツール（MCP含む）の実行詳細を記録
    """
    __tablename__ = "tool_execution_logs"

    # ツールログID
    tool_log_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # SDKセッションID
    session_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    # チャットセッションID
    chat_session_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), nullable=True, index=True
    )

    # ツール名
    # 例: Read, Write, Bash, mcp__servicenow__create_ticket
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    # ツール使用ID（SDK内の識別子）
    tool_use_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ツール入力（JSON）
    tool_input: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # ツール出力（JSON）
    tool_output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # ステータス (success / error)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")

    # 実行時間（ミリ秒）
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 実行日時
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<ToolExecutionLog(tool_log_id={self.tool_log_id}, tool_name={self.tool_name})>"
