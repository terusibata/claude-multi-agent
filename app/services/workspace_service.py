"""
ワークスペースサービス（S3版）

S3ベースのワークスペース管理を行う。
セッション専用ワークスペースのファイル操作はS3を経由する。
"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import structlog
from sqlalchemy import and_, select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.chat_session import ChatSession
from app.models.session_file import SessionFile
from app.schemas.workspace import (
    SessionFileInfo,
    WorkspaceContextForAI,
    WorkspaceFileList,
    WorkspaceInfo,
)
from app.services.workspace.s3_storage import S3StorageBackend
from app.services.workspace.context_builder import AIContextBuilder

# 後方互換性のため例外クラスを再エクスポート
from app.utils.exceptions import WorkspaceSecurityError

settings = get_settings()
logger = structlog.get_logger(__name__)


class WorkspaceService:
    """S3ベースのワークスペースサービス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db
        self.s3 = S3StorageBackend()
        self.context_builder = AIContextBuilder()

    async def upload_files(
        self,
        tenant_id: str,
        session_id: str,
        files: list[tuple[str, bytes, str]],  # [(filename, content, content_type), ...]
    ) -> list[SessionFileInfo]:
        """
        複数ファイルをS3にアップロード

        Args:
            tenant_id: テナントID
            session_id: セッションID
            files: [(filename, content, content_type), ...]

        Returns:
            アップロードされたファイル情報のリスト
        """
        results = []
        for filename, content, content_type in files:
            file_path = f"uploads/{filename}"
            await self.s3.upload(tenant_id, session_id, file_path, content, content_type)

            # DBに記録
            file_info = await self._save_file_record(
                tenant_id=tenant_id,
                session_id=session_id,
                file_path=file_path,
                original_name=filename,
                file_size=len(content),
                content_type=content_type,
                source="user_upload",
            )
            results.append(file_info)

        return results

    async def download_file(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> tuple[bytes, str, str]:
        """
        ファイルをS3からダウンロード

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス（相対パス）

        Returns:
            (content, filename, content_type)

        Raises:
            WorkspaceSecurityError: パスが無効な場合
        """
        # パストラバーサル攻撃の防止
        from app.utils.security import validate_path_traversal
        try:
            validate_path_traversal(file_path)
        except Exception as e:
            raise WorkspaceSecurityError(f"無効なファイルパス: {str(e)}")

        content, content_type = await self.s3.download(tenant_id, session_id, file_path)
        filename = file_path.split('/')[-1]
        return content, filename, content_type

    async def list_files(
        self,
        tenant_id: str,
        session_id: str,
    ) -> WorkspaceFileList:
        """
        ファイル一覧を取得（DBから）

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            ファイル一覧
        """
        # セッション所有権確認
        await self._verify_session_ownership(tenant_id, session_id)

        # DBからファイルレコードを取得
        files = await self._get_file_records(session_id)

        total_size = sum(f.file_size for f in files)

        return WorkspaceFileList(
            chat_session_id=session_id,
            files=files,
            total_count=len(files),
            total_size=total_size,
        )

    async def get_presented_files(
        self,
        tenant_id: str,
        session_id: str,
    ) -> list[SessionFileInfo]:
        """
        AIが作成したファイル一覧を取得

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            Presentedファイル一覧
        """
        # セッション所有権確認
        await self._verify_session_ownership(tenant_id, session_id)

        return await self._get_file_records(session_id, is_presented=True)

    async def register_ai_file(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        is_presented: bool = True,
    ) -> Optional[SessionFileInfo]:
        """
        AIが作成したファイルを登録

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス
            is_presented: Presentedフラグ

        Returns:
            登録されたファイル情報
        """
        # S3に存在するか確認
        if not await self.s3.exists(tenant_id, session_id, file_path):
            return None

        # ファイル情報取得
        content, content_type = await self.s3.download(tenant_id, session_id, file_path)

        return await self._save_file_record(
            tenant_id=tenant_id,
            session_id=session_id,
            file_path=file_path,
            original_name=file_path.split('/')[-1],
            file_size=len(content),
            content_type=content_type,
            source="ai_created",
            is_presented=is_presented,
        )

    def get_workspace_local_path(self, session_id: str) -> str:
        """
        一時ローカルパスを取得

        Args:
            session_id: セッションID

        Returns:
            ローカルパス

        Raises:
            WorkspaceSecurityError: セッションIDが無効な場合
        """
        # セッションIDのバリデーション（パストラバーサル防止）
        from app.utils.security import validate_session_id
        try:
            validate_session_id(session_id)
        except Exception as e:
            raise WorkspaceSecurityError(f"無効なセッションID: {str(e)}")

        # 設定ファイルからベースディレクトリを取得
        base_dir = settings.workspace_temp_dir
        workspace_path = Path(base_dir) / f"workspace_{session_id}"

        # ベースディレクトリが存在しない場合は作成
        Path(base_dir).mkdir(parents=True, exist_ok=True, mode=0o700)

        return str(workspace_path)

    async def sync_to_local(self, tenant_id: str, session_id: str) -> str:
        """
        S3からローカルに同期

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            ローカルディレクトリパス

        Raises:
            WorkspaceSecurityError: 同期に失敗した場合
        """
        local_dir = self.get_workspace_local_path(session_id)

        # ディレクトリを作成
        Path(local_dir).mkdir(parents=True, exist_ok=True)

        try:
            await self.s3.sync_to_local(tenant_id, session_id, local_dir)
        except FileNotFoundError:
            # ファイルがない場合は正常（新規セッション）
            logger.info(
                "S3にファイルがありません（新規セッション）",
                tenant_id=tenant_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.error(
                "S3→ローカル同期エラー",
                tenant_id=tenant_id,
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            raise WorkspaceSecurityError(
                f"ワークスペース同期に失敗しました: {str(e)}"
            )

        return local_dir

    async def sync_from_local(self, tenant_id: str, session_id: str) -> list[str]:
        """
        ローカルからS3に同期

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            同期されたファイルパスのリスト

        Raises:
            WorkspaceSecurityError: 同期に失敗した場合
        """
        local_dir = self.get_workspace_local_path(session_id)

        # ローカルディレクトリが存在しない場合は空リストを返す
        if not Path(local_dir).exists():
            logger.info(
                "ローカルディレクトリが存在しません",
                tenant_id=tenant_id,
                session_id=session_id,
                local_dir=local_dir,
            )
            return []

        try:
            return await self.s3.sync_from_local(tenant_id, session_id, local_dir)
        except Exception as e:
            logger.error(
                "ローカル→S3同期エラー",
                tenant_id=tenant_id,
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            raise WorkspaceSecurityError(
                f"ワークスペース同期に失敗しました: {str(e)}"
            )

    async def cleanup_local(self, session_id: str) -> None:
        """
        ローカルの一時ファイルを削除

        Args:
            session_id: セッションID
        """
        local_dir = self.get_workspace_local_path(session_id)

        def log_error(func, path, exc_info):
            """削除エラー時のコールバック"""
            logger.warning(
                "ローカルワークスペース削除エラー",
                session_id=session_id,
                path=path,
                error=str(exc_info[1]) if exc_info else "Unknown error",
            )

        if Path(local_dir).exists():
            shutil.rmtree(local_dir, onerror=log_error)
            logger.info("ローカルワークスペース削除完了", session_id=session_id)
        else:
            logger.debug("ローカルワークスペースは存在しません", session_id=session_id)

    def get_workspace_cwd(self, tenant_id: str, session_id: str) -> str:
        """
        セッション専用ワークスペースのcwd（作業ディレクトリ）を取得

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            cwdパス（ローカル一時ディレクトリ）
        """
        return self.get_workspace_local_path(session_id)

    async def get_workspace_info(
        self,
        tenant_id: str,
        session_id: str,
    ) -> Optional[WorkspaceInfo]:
        """
        ワークスペース情報を取得

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            ワークスペース情報（存在しない場合はNone）
        """
        # セッション取得
        result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.chat_session_id == session_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        session = result.scalar_one_or_none()
        if not session or not session.workspace_enabled:
            return None

        # ファイル統計を取得
        file_count, total_size = await self._get_file_stats(session_id)

        return WorkspaceInfo(
            chat_session_id=session_id,
            workspace_enabled=session.workspace_enabled,
            workspace_path=session.workspace_path,
            workspace_created_at=session.workspace_created_at,
            file_count=file_count,
            total_size=total_size,
        )

    async def enable_workspace(
        self,
        tenant_id: str,
        session_id: str,
    ) -> None:
        """
        ワークスペースを有効化

        Args:
            tenant_id: テナントID
            session_id: セッションID
        """
        now = datetime.utcnow()
        local_path = self.get_workspace_local_path(session_id)

        await self.db.execute(
            update(ChatSession)
            .where(
                and_(
                    ChatSession.chat_session_id == session_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
            .values(
                workspace_enabled=True,
                workspace_path=local_path,
                workspace_created_at=now,
            )
        )
        await self.db.flush()

        logger.info(
            "ワークスペース有効化完了",
            tenant_id=tenant_id,
            session_id=session_id,
        )

    async def get_context_for_ai(
        self,
        tenant_id: str,
        session_id: str,
    ) -> Optional[WorkspaceContextForAI]:
        """
        AIに提供するワークスペースコンテキストを生成

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            AIコンテキスト（ワークスペースが無効な場合はNone）
        """
        workspace_info = await self.get_workspace_info(tenant_id, session_id)
        if not workspace_info or not workspace_info.workspace_enabled:
            return None

        file_list = await self.list_files(tenant_id, session_id)

        return self.context_builder.build_context(workspace_info, file_list)

    async def _save_file_record(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        original_name: str,
        file_size: int,
        content_type: str,
        source: str,
        is_presented: bool = False,
    ) -> SessionFileInfo:
        """
        ファイルレコードをDBに保存

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス
            original_name: 元のファイル名
            file_size: ファイルサイズ
            content_type: MIMEタイプ
            source: ソース
            is_presented: Presentedフラグ

        Returns:
            ファイル情報
        """
        # バージョン取得
        new_version = await self._get_next_version(session_id, file_path)

        # DBに記録
        session_file = SessionFile(
            file_id=str(uuid4()),
            chat_session_id=session_id,
            file_path=file_path,
            original_name=original_name,
            file_size=file_size,
            mime_type=content_type,
            version=new_version,
            source=source,
            is_presented=is_presented,
            checksum=None,  # S3版ではチェックサムは省略
            description=None,
            status="active",
        )
        self.db.add(session_file)
        await self.db.flush()
        await self.db.refresh(session_file)

        logger.info(
            "ファイルレコード保存完了",
            session_id=session_id,
            file_path=file_path,
            version=new_version,
        )

        return self._to_file_info(session_file)

    async def _get_file_records(
        self,
        session_id: str,
        is_presented: Optional[bool] = None,
    ) -> list[SessionFileInfo]:
        """
        ファイルレコードを取得

        Args:
            session_id: セッションID
            is_presented: Presentedフラグでフィルタ

        Returns:
            ファイル情報のリスト
        """
        conditions = [
            SessionFile.chat_session_id == session_id,
            SessionFile.status == "active",
        ]

        if is_presented is not None:
            conditions.append(SessionFile.is_presented == is_presented)

        # 最新バージョンのみ取得
        subquery = (
            select(
                SessionFile.file_path,
                func.max(SessionFile.version).label("max_version"),
            )
            .where(and_(*conditions))
            .group_by(SessionFile.file_path)
            .subquery()
        )

        query = (
            select(SessionFile)
            .join(
                subquery,
                and_(
                    SessionFile.file_path == subquery.c.file_path,
                    SessionFile.version == subquery.c.max_version,
                ),
            )
            .where(and_(*conditions))
            .order_by(SessionFile.created_at.desc())
        )

        result = await self.db.execute(query)
        files = result.scalars().all()

        return [self._to_file_info(f) for f in files]

    async def _get_file_stats(
        self,
        session_id: str,
    ) -> tuple[int, int]:
        """
        ファイル統計を取得

        Args:
            session_id: セッションID

        Returns:
            (ファイル数, 合計サイズ)
        """
        stats = await self.db.execute(
            select(
                func.count(SessionFile.file_id).label("file_count"),
                func.coalesce(func.sum(SessionFile.file_size), 0).label("total_size"),
            ).where(
                and_(
                    SessionFile.chat_session_id == session_id,
                    SessionFile.status == "active",
                )
            )
        )
        row = stats.first()
        return (row.file_count if row else 0, row.total_size if row else 0)

    async def _get_next_version(
        self,
        session_id: str,
        file_path: str,
    ) -> int:
        """
        次のバージョン番号を取得

        Args:
            session_id: セッションID
            file_path: ファイルパス

        Returns:
            次のバージョン番号
        """
        result = await self.db.execute(
            select(func.max(SessionFile.version)).where(
                and_(
                    SessionFile.chat_session_id == session_id,
                    SessionFile.file_path == file_path,
                    SessionFile.status == "active",
                )
            )
        )
        max_version = result.scalar()
        return (max_version + 1) if max_version else 1

    async def _verify_session_ownership(
        self,
        tenant_id: str,
        session_id: str,
    ) -> None:
        """
        セッション所有権を確認

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Raises:
            WorkspaceSecurityError: アクセス拒否
        """
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.chat_session_id == session_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        if not session_result.scalar_one_or_none():
            raise WorkspaceSecurityError("セッションへのアクセスが拒否されました")

    def _to_file_info(self, session_file: SessionFile) -> SessionFileInfo:
        """
        SessionFileをSessionFileInfoに変換

        Args:
            session_file: SessionFileモデル

        Returns:
            SessionFileInfo
        """
        return SessionFileInfo(
            file_id=session_file.file_id,
            file_path=session_file.file_path,
            original_name=session_file.original_name,
            file_size=session_file.file_size,
            mime_type=session_file.mime_type,
            version=session_file.version,
            source=session_file.source,
            is_presented=session_file.is_presented,
            checksum=session_file.checksum,
            description=session_file.description,
            created_at=session_file.created_at,
            updated_at=session_file.updated_at,
        )
