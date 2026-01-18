"""
使用状況・コストサービス
トークン使用量とコストの記録・レポート生成
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.tool_execution_log import ToolExecutionLog
from app.models.usage_log import UsageLog


class UsageService:
    """使用状況・コストサービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

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
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> UsageLog:
        """
        使用状況ログを保存

        Args:
            tenant_id: テナントID
            user_id: ユーザーID
            model_id: モデルID
            input_tokens: 入力トークン数
            output_tokens: 出力トークン数
            cache_creation_5m_tokens: 5分キャッシュ作成トークン数
            cache_creation_1h_tokens: 1時間キャッシュ作成トークン数
            cache_read_tokens: キャッシュ読み込みトークン数
            cost_usd: コスト（USD）
            session_id: SDKセッションID
            conversation_id: 会話ID

        Returns:
            保存された使用状況ログ
        """
        total_tokens = (
            input_tokens + output_tokens
            + cache_creation_5m_tokens + cache_creation_1h_tokens
            + cache_read_tokens
        )

        log = UsageLog(
            usage_log_id=str(uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            model_id=model_id,
            session_id=session_id,
            conversation_id=conversation_id,
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
        user_id: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UsageLog]:
        """
        使用状況ログを取得

        Args:
            tenant_id: テナントID
            user_id: フィルタリング用ユーザーID
            from_date: 開始日時
            to_date: 終了日時
            limit: 取得件数
            offset: オフセット

        Returns:
            使用状況ログリスト
        """
        query = select(UsageLog).where(UsageLog.tenant_id == tenant_id)

        if user_id:
            query = query.where(UsageLog.user_id == user_id)
        if from_date:
            query = query.where(UsageLog.executed_at >= from_date)
        if to_date:
            query = query.where(UsageLog.executed_at <= to_date)

        query = query.order_by(UsageLog.executed_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_usage_summary(
        self,
        tenant_id: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        group_by: str = "day",
    ) -> list[dict]:
        """
        使用状況サマリーを取得

        Args:
            tenant_id: テナントID
            from_date: 開始日時
            to_date: 終了日時
            group_by: グループ化単位 (day / week / month)

        Returns:
            使用状況サマリーリスト
        """
        # グループ化関数の決定
        if group_by == "month":
            date_trunc = func.date_trunc("month", UsageLog.executed_at)
        elif group_by == "week":
            date_trunc = func.date_trunc("week", UsageLog.executed_at)
        else:
            date_trunc = func.date_trunc("day", UsageLog.executed_at)

        # クエリ構築
        query = select(
            date_trunc.label("period"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.input_tokens).label("input_tokens"),
            func.sum(UsageLog.output_tokens).label("output_tokens"),
            func.sum(UsageLog.cache_creation_5m_tokens).label("cache_creation_5m_tokens"),
            func.sum(UsageLog.cache_creation_1h_tokens).label("cache_creation_1h_tokens"),
            func.sum(UsageLog.cache_read_tokens).label("cache_read_tokens"),
            func.sum(UsageLog.cost_usd).label("total_cost_usd"),
            func.count(UsageLog.usage_log_id).label("execution_count"),
        ).where(UsageLog.tenant_id == tenant_id)

        if from_date:
            query = query.where(UsageLog.executed_at >= from_date)
        if to_date:
            query = query.where(UsageLog.executed_at <= to_date)

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

    async def get_cost_report(
        self,
        tenant_id: str,
        from_date: datetime,
        to_date: datetime,
        model_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict:
        """
        コストレポートを生成

        Args:
            tenant_id: テナントID
            from_date: 開始日時
            to_date: 終了日時
            model_id: フィルタリング用モデルID
            user_id: フィルタリング用ユーザーID

        Returns:
            コストレポート
        """
        # モデル別集計
        model_query = select(
            UsageLog.model_id,
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.input_tokens).label("input_tokens"),
            func.sum(UsageLog.output_tokens).label("output_tokens"),
            func.sum(UsageLog.cache_creation_5m_tokens).label("cache_creation_5m_tokens"),
            func.sum(UsageLog.cache_creation_1h_tokens).label("cache_creation_1h_tokens"),
            func.sum(UsageLog.cache_read_tokens).label("cache_read_tokens"),
            func.sum(UsageLog.cost_usd).label("cost_usd"),
            func.count(UsageLog.usage_log_id).label("execution_count"),
        ).where(
            and_(
                UsageLog.tenant_id == tenant_id,
                UsageLog.executed_at >= from_date,
                UsageLog.executed_at <= to_date,
            )
        )

        if model_id:
            model_query = model_query.where(UsageLog.model_id == model_id)
        if user_id:
            model_query = model_query.where(UsageLog.user_id == user_id)

        model_query = model_query.group_by(UsageLog.model_id)
        model_result = await self.db.execute(model_query)
        model_rows = model_result.all()

        # モデル名を取得
        model_ids = [row.model_id for row in model_rows]
        models_query = select(Model).where(Model.model_id.in_(model_ids))
        models_result = await self.db.execute(models_query)
        models = {m.model_id: m.display_name for m in models_result.scalars().all()}

        by_model = [
            {
                "model_id": row.model_id,
                "model_name": models.get(row.model_id, row.model_id),
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

        # 合計計算
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
        tool_use_id: Optional[str] = None,
        tool_input: Optional[dict] = None,
        tool_output: Optional[dict] = None,
        status: str = "success",
        execution_time_ms: Optional[int] = None,
        conversation_id: Optional[str] = None,
    ) -> ToolExecutionLog:
        """
        ツール実行ログを保存

        Args:
            session_id: SDKセッションID
            tool_name: ツール名
            tool_use_id: ツール使用ID
            tool_input: ツール入力
            tool_output: ツール出力
            status: ステータス
            execution_time_ms: 実行時間（ミリ秒）
            conversation_id: 会話ID

        Returns:
            保存されたツール実行ログ
        """
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
        session_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ToolExecutionLog]:
        """
        ツール実行ログを取得

        Args:
            tenant_id: テナントID（権限チェック用）
            session_id: フィルタリング用セッションID
            tool_name: フィルタリング用ツール名
            from_date: 開始日時
            to_date: 終了日時
            limit: 取得件数
            offset: オフセット

        Returns:
            ツール実行ログリスト
        """
        query = select(ToolExecutionLog)

        if session_id:
            query = query.where(ToolExecutionLog.session_id == session_id)
        if tool_name:
            query = query.where(ToolExecutionLog.tool_name == tool_name)
        if from_date:
            query = query.where(ToolExecutionLog.executed_at >= from_date)
        if to_date:
            query = query.where(ToolExecutionLog.executed_at <= to_date)

        query = query.order_by(ToolExecutionLog.executed_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())
