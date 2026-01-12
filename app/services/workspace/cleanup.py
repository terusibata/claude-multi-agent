"""
ワークスペースクリーンアップマネージャー
古いワークスペースのクリーンアップを担当
"""
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession

logger = structlog.get_logger(__name__)


class CleanupManager:
    """
    クリーンアップマネージャー

    古いワークスペースの削除を管理
    """

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

    async def cleanup_old_workspaces(
        self,
        tenant_id: str,
        older_than_days: int = 30,
        dry_run: bool = True,
    ) -> dict:
        """
        古いワークスペースをクリーンアップ

        Args:
            tenant_id: テナントID
            older_than_days: この日数より古いワークスペースを対象
            dry_run: ドライラン（削除せずにリストのみ返す）

        Returns:
            クリーンアップ結果
        """
        cutoff_date = datetime.utcnow() - timedelta(days=older_than_days)

        # 古いセッションを取得
        result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.workspace_enabled == True,
                    ChatSession.updated_at < cutoff_date,
                    ChatSession.status == "archived",
                )
            )
        )
        old_sessions = result.scalars().all()

        cleaned_sessions = []
        total_size_freed = 0

        for session in old_sessions:
            workspace_path = Path(session.workspace_path) if session.workspace_path else None

            if workspace_path and workspace_path.exists():
                # ディレクトリサイズを計算
                size = self._calculate_directory_size(workspace_path)
                total_size_freed += size

                if not dry_run:
                    # ディレクトリ削除
                    shutil.rmtree(workspace_path)

                    # DB更新
                    session.workspace_enabled = False
                    session.workspace_path = None

                cleaned_sessions.append(session.chat_session_id)

        if not dry_run:
            await self.db.flush()

        logger.info(
            "ワークスペースクリーンアップ",
            tenant_id=tenant_id,
            sessions_count=len(cleaned_sessions),
            total_size_freed=total_size_freed,
            dry_run=dry_run,
        )

        return {
            "success": True,
            "sessions_cleaned": len(cleaned_sessions),
            "total_size_freed": total_size_freed,
            "sessions": cleaned_sessions,
            "dry_run": dry_run,
        }

    def _calculate_directory_size(self, path: Path) -> int:
        """ディレクトリサイズを計算"""
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
