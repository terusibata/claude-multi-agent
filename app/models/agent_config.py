"""
エージェント実行設定テーブル
テナントごとのエージェント実行設定を管理
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentConfig(Base):
    """
    エージェント実行設定テーブル
    Claude Agent SDKのClaudeAgentOptionsに対応する設定を管理
    """
    __tablename__ = "agent_configs"

    # 内部管理ID
    agent_config_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    # テナントID
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # 設定名
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 説明
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # システムプロンプト
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 使用するモデルID（モデル定義テーブル参照）
    model_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("models.model_id"), nullable=False
    )

    # 許可するツールのリスト（JSON配列）
    # 例: ["Read", "Write", "Bash", "Skill"]
    allowed_tools: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # 権限モード: default / acceptEdits / bypassPermissions / plan
    permission_mode: Mapped[str] = mapped_column(
        String(50), nullable=False, default="default"
    )

    # 使用するAgent Skills名のリスト（JSON配列）
    # 例: ["servicenow-operations", "sales-automation"]
    agent_skills: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # 使用するMCPサーバーIDのリスト（JSON配列）
    mcp_servers: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # ワークスペース設定
    # セッション専用ワークスペースを有効にするか
    workspace_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ワークスペース自動クリーンアップ日数（0=無効）
    workspace_auto_cleanup_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )

    # ステータス (active / inactive)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # リレーションシップ
    model = relationship("Model", lazy="selectin")

    def __repr__(self) -> str:
        return f"<AgentConfig(agent_config_id={self.agent_config_id}, name={self.name})>"
