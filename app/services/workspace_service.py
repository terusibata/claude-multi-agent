"""
ワークスペースサービス
セッション専用ワークスペースの管理

セキュリティ要件：
- セッション専用ワークスペース以外へのアクセスを絶対に禁止
- パストラバーサル攻撃の防止
- テナント間のアイソレーション
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.chat_session import ChatSession
from app.schemas.workspace import (
    SessionFileInfo,
    WorkspaceContextForAI,
    WorkspaceFileList,
    WorkspaceInfo,
)
from app.services.workspace import (
    AIContextBuilder,
    CleanupManager,
    FileManager,
    PathValidator,
)

# 後方互換性のため例外クラスを再エクスポート
from app.utils.exceptions import WorkspaceSecurityError

settings = get_settings()
logger = structlog.get_logger(__name__)


class WorkspaceService:
    """
    セッション専用ワークスペースサービス

    セキュリティ原則：
    1. すべてのパスは正規化後に検証
    2. ワークスペースルート外へのアクセスは絶対禁止
    3. テナントIDとセッションIDの両方で検証
    """

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db
        self.base_path = Path(settings.skills_base_path)

        # サブモジュールを初期化
        self.path_validator = PathValidator(self.base_path)
        self.file_manager = FileManager(db, self.path_validator)
        self.context_builder = AIContextBuilder()
        self.cleanup_manager = CleanupManager(db)

    async def create_workspace(
        self,
        tenant_id: str,
        chat_session_id: str,
    ) -> WorkspaceInfo:
        """
        セッション専用ワークスペースを作成

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            ワークスペース情報
        """
        workspace_root = self.path_validator.get_workspace_root(
            tenant_id, chat_session_id
        )

        # ディレクトリ作成
        workspace_root.mkdir(parents=True, exist_ok=True)

        # サブディレクトリを作成
        (workspace_root / "uploads").mkdir(exist_ok=True)
        (workspace_root / "outputs").mkdir(exist_ok=True)
        (workspace_root / "temp").mkdir(exist_ok=True)

        # セッション情報を更新
        now = datetime.utcnow()
        await self.db.execute(
            update(ChatSession)
            .where(
                and_(
                    ChatSession.chat_session_id == chat_session_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
            .values(
                workspace_enabled=True,
                workspace_path=str(workspace_root),
                workspace_created_at=now,
            )
        )
        await self.db.flush()

        logger.info(
            "ワークスペース作成完了",
            tenant_id=tenant_id,
            chat_session_id=chat_session_id,
            workspace_path=str(workspace_root),
        )

        return WorkspaceInfo(
            chat_session_id=chat_session_id,
            workspace_enabled=True,
            workspace_path=str(workspace_root),
            workspace_created_at=now,
            file_count=0,
            total_size=0,
        )

    async def get_workspace_info(
        self,
        tenant_id: str,
        chat_session_id: str,
    ) -> Optional[WorkspaceInfo]:
        """
        ワークスペース情報を取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            ワークスペース情報（存在しない場合はNone）
        """
        # セッション取得
        result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.chat_session_id == chat_session_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        session = result.scalar_one_or_none()
        if not session or not session.workspace_enabled:
            return None

        # ファイル統計を取得
        file_count, total_size = await self.file_manager.get_file_stats(chat_session_id)

        return WorkspaceInfo(
            chat_session_id=chat_session_id,
            workspace_enabled=session.workspace_enabled,
            workspace_path=session.workspace_path,
            workspace_created_at=session.workspace_created_at,
            file_count=file_count,
            total_size=total_size,
        )

    async def upload_file(
        self,
        tenant_id: str,
        chat_session_id: str,
        file_path: str,
        content: bytes,
        original_name: str,
        description: Optional[str] = None,
    ) -> SessionFileInfo:
        """
        ファイルをワークスペースにアップロード

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID
            file_path: 保存先パス（ワークスペース内）
            content: ファイル内容
            original_name: 元のファイル名
            description: ファイル説明

        Returns:
            アップロードされたファイル情報
        """
        # ワークスペースの存在確認と作成
        workspace_info = await self.get_workspace_info(tenant_id, chat_session_id)
        if not workspace_info:
            await self.create_workspace(tenant_id, chat_session_id)
            workspace_info = await self.get_workspace_info(tenant_id, chat_session_id)

        current_total_size = workspace_info.total_size if workspace_info else 0

        return await self.file_manager.upload_file(
            tenant_id=tenant_id,
            chat_session_id=chat_session_id,
            file_path=file_path,
            content=content,
            original_name=original_name,
            description=description,
            current_total_size=current_total_size,
        )

    async def register_ai_file(
        self,
        tenant_id: str,
        chat_session_id: str,
        file_path: str,
        source: str = "ai_created",
        is_presented: bool = False,
        description: Optional[str] = None,
    ) -> Optional[SessionFileInfo]:
        """
        AIが作成/編集したファイルを登録

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID
            file_path: ファイルパス（ワークスペース内）
            source: ソース ("ai_created" or "ai_modified")
            is_presented: Presentedフラグ
            description: ファイル説明

        Returns:
            登録されたファイル情報
        """
        return await self.file_manager.register_ai_file(
            tenant_id=tenant_id,
            chat_session_id=chat_session_id,
            file_path=file_path,
            source=source,
            is_presented=is_presented,
            description=description,
        )

    async def list_files(
        self,
        tenant_id: str,
        chat_session_id: str,
        include_all_versions: bool = False,
    ) -> WorkspaceFileList:
        """
        ワークスペース内のファイル一覧を取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID
            include_all_versions: 全バージョンを含めるか

        Returns:
            ファイル一覧
        """
        # セキュリティ検証
        self.path_validator.validate_id(tenant_id, "tenant_id")
        self.path_validator.validate_id(chat_session_id, "chat_session_id")

        # セッション所有権確認
        await self._verify_session_ownership(tenant_id, chat_session_id)

        return await self.file_manager.list_files(
            chat_session_id=chat_session_id,
            include_all_versions=include_all_versions,
        )

    async def download_file(
        self,
        tenant_id: str,
        chat_session_id: str,
        file_path: str,
        version: Optional[int] = None,
    ) -> tuple[bytes, str, str]:
        """
        ファイルをダウンロード

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID
            file_path: ファイルパス
            version: バージョン（省略時は最新）

        Returns:
            (ファイル内容, ファイル名, MIMEタイプ)
        """
        # セッション所有権確認
        await self._verify_session_ownership(tenant_id, chat_session_id)

        return await self.file_manager.download_file(
            tenant_id=tenant_id,
            chat_session_id=chat_session_id,
            file_path=file_path,
            version=version,
        )

    async def set_presented(
        self,
        tenant_id: str,
        chat_session_id: str,
        file_path: str,
        description: Optional[str] = None,
    ) -> Optional[SessionFileInfo]:
        """
        ファイルをPresentedとしてマーク

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID
            file_path: ファイルパス
            description: 説明（更新する場合）

        Returns:
            更新されたファイル情報
        """
        return await self.file_manager.set_presented(
            chat_session_id=chat_session_id,
            file_path=file_path,
            description=description,
        )

    async def get_presented_files(
        self,
        tenant_id: str,
        chat_session_id: str,
    ) -> list[SessionFileInfo]:
        """
        Presentedファイル一覧を取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            Presentedファイル一覧
        """
        # セッション所有権確認
        await self._verify_session_ownership(tenant_id, chat_session_id)

        return await self.file_manager.get_presented_files(chat_session_id)

    def get_workspace_cwd(self, tenant_id: str, chat_session_id: str) -> str:
        """
        セッション専用ワークスペースのcwd（作業ディレクトリ）を取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            cwdパス
        """
        return self.path_validator.get_workspace_cwd(tenant_id, chat_session_id)

    async def get_context_for_ai(
        self,
        tenant_id: str,
        chat_session_id: str,
    ) -> Optional[WorkspaceContextForAI]:
        """
        AIに提供するワークスペースコンテキストを生成

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            AIコンテキスト（ワークスペースが無効な場合はNone）
        """
        workspace_info = await self.get_workspace_info(tenant_id, chat_session_id)
        if not workspace_info or not workspace_info.workspace_enabled:
            return None

        file_list = await self.list_files(tenant_id, chat_session_id)

        return self.context_builder.build_context(workspace_info, file_list)

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
        return await self.cleanup_manager.cleanup_old_workspaces(
            tenant_id=tenant_id,
            older_than_days=older_than_days,
            dry_run=dry_run,
        )

    async def _verify_session_ownership(
        self,
        tenant_id: str,
        chat_session_id: str,
    ) -> None:
        """
        セッション所有権を確認

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Raises:
            WorkspaceSecurityError: アクセス拒否
        """
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.chat_session_id == chat_session_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        if not session_result.scalar_one_or_none():
            raise WorkspaceSecurityError("セッションへのアクセスが拒否されました")
