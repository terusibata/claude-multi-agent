"""
使用状況・コストレポートスキーマ
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class UsageLogResponse(BaseModel):
    """使用状況ログレスポンス"""

    usage_log_id: str
    tenant_id: str
    user_id: str
    model_id: str
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    input_tokens: int
    output_tokens: int
    cache_creation_5m_tokens: int
    cache_creation_1h_tokens: int
    cache_read_tokens: int
    total_tokens: int
    cost_usd: Decimal
    executed_at: datetime

    class Config:
        from_attributes = True


class UsageSummary(BaseModel):
    """使用状況サマリー"""

    period: str  # 例: "2024-01", "2024-01-15"
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    cache_read_tokens: int = 0
    total_cost_usd: Decimal = Decimal("0")
    execution_count: int = 0


class UsageQuery(BaseModel):
    """使用状況クエリ"""

    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    group_by: str = Field(default="day", pattern="^(day|week|month)$")


class CostReportItem(BaseModel):
    """コストレポート項目"""

    model_id: str
    model_name: str
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_creation_5m_tokens: int
    cache_creation_1h_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal
    execution_count: int


class CostReportResponse(BaseModel):
    """コストレポートレスポンス"""

    tenant_id: str
    from_date: datetime
    to_date: datetime
    total_cost_usd: Decimal
    total_tokens: int
    total_executions: int
    by_model: list[CostReportItem]
    by_user: Optional[list[dict]] = None


class ToolLogQuery(BaseModel):
    """ツール実行ログクエリ"""

    session_id: Optional[str] = None
    tool_name: Optional[str] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ToolLogResponse(BaseModel):
    """ツール実行ログレスポンス"""

    tool_log_id: str
    session_id: str
    conversation_id: Optional[str] = None
    tool_name: str
    tool_use_id: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output: Optional[dict] = None
    status: str
    execution_time_ms: Optional[int] = None
    executed_at: datetime

    class Config:
        from_attributes = True
