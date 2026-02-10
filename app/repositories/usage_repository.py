"""
使用状況リポジトリ
"""
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_execution_log import ToolExecutionLog
from app.models.usage_log import UsageLog
from app.repositories.base import BaseRepository
from app.utils.timezone import to_utc


class UsageRepository(BaseRepository[UsageLog]):
    """使用状況ログのデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(db, UsageLog, id_field="usage_log_id", tenant_field="tenant_id")

    async def find_by_tenant(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UsageLog]:
        """テナントの使用状況ログを取得"""
        from_date_utc = to_utc(from_date)
        to_date_utc = to_utc(to_date)

        query = select(UsageLog).where(UsageLog.tenant_id == tenant_id)

        if user_id:
            query = query.where(UsageLog.user_id == user_id)
        if from_date_utc:
            query = query.where(UsageLog.executed_at >= from_date_utc)
        if to_date_utc:
            query = query.where(UsageLog.executed_at <= to_date_utc)

        query = query.order_by(UsageLog.executed_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_summary(
        self,
        tenant_id: str,
        *,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        group_by: str = "day",
    ) -> list[dict]:
        """使用状況サマリーを取得"""
        from_date_utc = to_utc(from_date)
        to_date_utc = to_utc(to_date)

        date_trunc = func.date_trunc(group_by, UsageLog.executed_at)

        query = (
            select(
                date_trunc.label("period"),
                func.sum(UsageLog.total_tokens).label("total_tokens"),
                func.sum(UsageLog.input_tokens).label("input_tokens"),
                func.sum(UsageLog.output_tokens).label("output_tokens"),
                func.sum(UsageLog.cache_creation_5m_tokens).label(
                    "cache_creation_5m_tokens"
                ),
                func.sum(UsageLog.cache_creation_1h_tokens).label(
                    "cache_creation_1h_tokens"
                ),
                func.sum(UsageLog.cache_read_tokens).label("cache_read_tokens"),
                func.sum(UsageLog.cost_usd).label("total_cost_usd"),
                func.count(UsageLog.usage_log_id).label("execution_count"),
            )
            .where(UsageLog.tenant_id == tenant_id)
        )

        if from_date_utc:
            query = query.where(UsageLog.executed_at >= from_date_utc)
        if to_date_utc:
            query = query.where(UsageLog.executed_at <= to_date_utc)

        query = query.group_by(date_trunc).order_by(date_trunc)

        result = await self.db.execute(query)
        rows = result.all()

        return [
            {
                "period": str(row.period),
                "total_tokens": row.total_tokens or 0,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "cache_creation_5m_tokens": row.cache_creation_5m_tokens or 0,
                "cache_creation_1h_tokens": row.cache_creation_1h_tokens or 0,
                "cache_read_tokens": row.cache_read_tokens or 0,
                "total_cost_usd": float(row.total_cost_usd or 0),
                "execution_count": row.execution_count or 0,
            }
            for row in rows
        ]

    async def get_cost_by_model(
        self,
        tenant_id: str,
        from_date: datetime,
        to_date: datetime,
        *,
        model_id: str | None = None,
        user_id: str | None = None,
    ) -> list:
        """モデル別コスト集計を取得"""
        from_date_utc = to_utc(from_date)
        to_date_utc = to_utc(to_date)

        query = (
            select(
                UsageLog.model_id,
                func.sum(UsageLog.total_tokens).label("total_tokens"),
                func.sum(UsageLog.input_tokens).label("input_tokens"),
                func.sum(UsageLog.output_tokens).label("output_tokens"),
                func.sum(UsageLog.cache_creation_5m_tokens).label(
                    "cache_creation_5m_tokens"
                ),
                func.sum(UsageLog.cache_creation_1h_tokens).label(
                    "cache_creation_1h_tokens"
                ),
                func.sum(UsageLog.cache_read_tokens).label("cache_read_tokens"),
                func.sum(UsageLog.cost_usd).label("cost_usd"),
                func.count(UsageLog.usage_log_id).label("execution_count"),
            )
            .where(
                and_(
                    UsageLog.tenant_id == tenant_id,
                    UsageLog.executed_at >= from_date_utc,
                    UsageLog.executed_at <= to_date_utc,
                )
            )
        )

        if model_id:
            query = query.where(UsageLog.model_id == model_id)
        if user_id:
            query = query.where(UsageLog.user_id == user_id)

        query = query.group_by(UsageLog.model_id)
        result = await self.db.execute(query)
        return list(result.all())


class ToolExecutionLogRepository(BaseRepository[ToolExecutionLog]):
    """ツール実行ログのデータアクセス"""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ToolExecutionLog, id_field="tool_log_id")

    async def find_logs(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ToolExecutionLog]:
        """ツール実行ログを検索"""
        from_date_utc = to_utc(from_date)
        to_date_utc = to_utc(to_date)

        query = select(ToolExecutionLog)

        if session_id:
            query = query.where(ToolExecutionLog.session_id == session_id)
        if tool_name:
            query = query.where(ToolExecutionLog.tool_name == tool_name)
        if from_date_utc:
            query = query.where(ToolExecutionLog.executed_at >= from_date_utc)
        if to_date_utc:
            query = query.where(ToolExecutionLog.executed_at <= to_date_utc)

        query = query.order_by(ToolExecutionLog.executed_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())
