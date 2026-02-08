"""
使用状況・コストサービス
トークン使用量とコストの記録・レポート生成
"""
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_execution_log import ToolExecutionLog
from app.models.usage_log import UsageLog
from app.repositories.model_repository import ModelRepository
from app.repositories.usage_repository import ToolExecutionLogRepository, UsageRepository


class UsageService:
    """使用状況・コストサービスクラス"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = UsageRepository(db)
        self.tool_repo = ToolExecutionLogRepository(db)
        self.model_repo = ModelRepository(db)

    # ============================================
    # 使用状況ログ操作
    # ============================================

    async def save_usage_log(
        self,
        tenant_id: str,
        user_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_5m_tokens: int = 0,
        cache_creation_1h_tokens: int = 0,
        cache_read_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"),
        session_id: str | None = None,
        conversation_id: str | None = None,
        simple_chat_id: str | None = None,
    ) -> UsageLog:
        """使用状況ログを保存"""
        total_tokens = (
            input_tokens
            + output_tokens
            + cache_creation_5m_tokens
            + cache_creation_1h_tokens
            + cache_read_tokens
        )

        log = UsageLog(
            usage_log_id=str(uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            model_id=model_id,
            session_id=session_id,
            conversation_id=conversation_id,
            simple_chat_id=simple_chat_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_5m_tokens=cache_creation_5m_tokens,
            cache_creation_1h_tokens=cache_creation_1h_tokens,
            cache_read_tokens=cache_read_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
        )
        self.db.add(log)
        await self.db.flush()
        return log

    async def get_usage_logs(
        self,
        tenant_id: str,
        user_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UsageLog]:
        """使用状況ログを取得"""
        return await self.repo.find_by_tenant(
            tenant_id,
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )

    async def get_usage_summary(
        self,
        tenant_id: str,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        group_by: str = "day",
    ) -> list[dict]:
        """使用状況サマリーを取得"""
        return await self.repo.get_summary(
            tenant_id,
            from_date=from_date,
            to_date=to_date,
            group_by=group_by,
        )

    async def get_cost_report(
        self,
        tenant_id: str,
        from_date: datetime,
        to_date: datetime,
        model_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """コストレポートを生成"""
        model_rows = await self.repo.get_cost_by_model(
            tenant_id,
            from_date,
            to_date,
            model_id=model_id,
            user_id=user_id,
        )

        # モデル名を取得
        model_ids = [row.model_id for row in model_rows]
        models = await self.model_repo.get_by_ids(model_ids)

        by_model = [
            {
                "model_id": row.model_id,
                "model_name": (
                    models[row.model_id].display_name
                    if row.model_id in models
                    else row.model_id
                ),
                "total_tokens": row.total_tokens or 0,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "cache_creation_5m_tokens": row.cache_creation_5m_tokens or 0,
                "cache_creation_1h_tokens": row.cache_creation_1h_tokens or 0,
                "cache_read_tokens": row.cache_read_tokens or 0,
                "cost_usd": float(row.cost_usd or 0),
                "execution_count": row.execution_count or 0,
            }
            for row in model_rows
        ]

        total_cost = sum(item["cost_usd"] for item in by_model)
        total_tokens = sum(item["total_tokens"] for item in by_model)
        total_executions = sum(item["execution_count"] for item in by_model)

        return {
            "tenant_id": tenant_id,
            "from_date": from_date,
            "to_date": to_date,
            "total_cost_usd": total_cost,
            "total_tokens": total_tokens,
            "total_executions": total_executions,
            "by_model": by_model,
        }

    # ============================================
    # ツール実行ログ操作
    # ============================================

    async def save_tool_log(
        self,
        session_id: str,
        tool_name: str,
        tool_use_id: str | None = None,
        tool_input: dict | None = None,
        tool_output: dict | None = None,
        status: str = "success",
        execution_time_ms: int | None = None,
        conversation_id: str | None = None,
    ) -> ToolExecutionLog:
        """ツール実行ログを保存"""
        log = ToolExecutionLog(
            tool_log_id=str(uuid4()),
            session_id=session_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            tool_input=tool_input,
            tool_output=tool_output,
            status=status,
            execution_time_ms=execution_time_ms,
        )
        self.db.add(log)
        await self.db.flush()
        return log

    async def get_tool_logs(
        self,
        tenant_id: str,
        session_id: str | None = None,
        tool_name: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ToolExecutionLog]:
        """ツール実行ログを取得"""
        return await self.tool_repo.find_logs(
            session_id=session_id,
            tool_name=tool_name,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )
