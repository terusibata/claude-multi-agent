"""
ワークスペースサービス（S3版）

S3ベースのワークスペース管理を行う。
会話専用ワークスペースのファイル操作はS3を経由する。
"""
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import structlog
from fastapi import UploadFile
from sqlalchemy import and_, select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.utils.exceptions import FileSizeError
from app.models.conversation import Conversation
from app.models.conversation_file import ConversationFile
from app.schemas.workspace import (
    ConversationFileInfo,
    FileUploadMetadata,
    WorkspaceContextForAI,
    WorkspaceFileList,
    WorkspaceInfo,
)
from app.services.workspace.s3_storage import S3StorageBackend
from app.services.workspace.context_builder import AIContextBuilder
from app.services.workspace.file_processors import FileTypeClassifier

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

    async def upload_user_file_with_metadata(
        self,
        tenant_id: str,
        conversation_id: str,
        file: UploadFile,
        metadata: FileUploadMetadata,
    ) -> ConversationFileInfo:
        """
        メタデータ付きでファイルをS3にアップロード

        フロントエンドで組み立てた識別子付きパスをそのまま使用する。
        バックエンド側でのパス組み立てロジックは不要。

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
            file: FastAPI UploadFile オブジェクト
            metadata: ファイルメタデータ（フロントエンドから送信）

        Returns:
            アップロードされたファイル情報

        Raises:
            FileSizeError: ファイルサイズが制限を超えた場合
        """
        content_type = metadata.content_type

        # ファイルタイプ別のサイズ制限を取得
        max_size = FileTypeClassifier.get_max_file_size(metadata.filename, content_type)

        # 申告サイズチェック
        if metadata.size > max_size:
            raise FileSizeError(
                filename=metadata.filename,
                size=metadata.size,
                max_size=max_size,
            )

        # フロントエンドで組み立て済みのパスをそのまま使用
        file_path = f"uploads/{metadata.relative_path}"

        # S3にストリームアップロード（メモリ効率化：ファイル全体をメモリに読み込まない）
        await self.s3.upload_stream(
            tenant_id, conversation_id, file_path,
            file.file,  # SpooledTemporaryFile を直接ストリーム
            content_type,
        )

        # DBに記録（original_relative_path 含む）
        file_info = await self._save_file_record(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path=file_path,
            original_name=metadata.original_name,
            original_relative_path=metadata.original_relative_path,
            file_size=metadata.size,
            content_type=content_type,
            source="user_upload",
        )

        return file_info

    async def download_file(
        self,
        tenant_id: str,
        conversation_id: str,
        file_path: str,
    ) -> tuple[bytes, str, str]:
        """
        ファイルをS3からダウンロード

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
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

        content, content_type = await self.s3.download(tenant_id, conversation_id, file_path)
        filename = file_path.split('/')[-1]
        return content, filename, content_type

    async def list_files(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> WorkspaceFileList:
        """
        ファイル一覧を取得（DBから）

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Returns:
            ファイル一覧
        """
        # 会話所有権確認
        await self._verify_conversation_ownership(tenant_id, conversation_id)

        # DBからファイルレコードを取得
        files = await self._get_file_records(conversation_id)

        total_size = sum(f.file_size for f in files)

        return WorkspaceFileList(
            conversation_id=conversation_id,
            files=files,
            total_count=len(files),
            total_size=total_size,
        )

    async def get_presented_files(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> list[ConversationFileInfo]:
        """
        AIが作成したファイル一覧を取得

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Returns:
            Presentedファイル一覧
        """
        # 会話所有権確認
        await self._verify_conversation_ownership(tenant_id, conversation_id)

        return await self._get_file_records(conversation_id, is_presented=True)

    async def register_ai_file(
        self,
        tenant_id: str,
        conversation_id: str,
        file_path: str,
        is_presented: bool = True,
    ) -> Optional[ConversationFileInfo]:
        """
        AIが作成したファイルを登録

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
            file_path: ファイルパス
            is_presented: Presentedフラグ

        Returns:
            登録されたファイル情報
        """
        # S3に存在するか確認
        if not await self.s3.exists(tenant_id, conversation_id, file_path):
            return None

        # メタデータのみ取得（ファイル全体をダウンロードしない）
        metadata = await self.s3.get_metadata(tenant_id, conversation_id, file_path)

        return await self._save_file_record(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path=file_path,
            original_name=file_path.split('/')[-1],
            file_size=metadata['content_length'],
            content_type=metadata['content_type'],
            source="ai_created",
            is_presented=is_presented,
        )

    def get_workspace_local_path(self, conversation_id: str) -> str:
        """
        一時ローカルパスを取得

        Args:
            conversation_id: 会話ID

        Returns:
            ローカルパス

        Raises:
            WorkspaceSecurityError: 会話IDが無効な場合
        """
        # 会話IDのバリデーション（パストラバーサル防止）
        from app.utils.security import validate_conversation_id
        try:
            validate_conversation_id(conversation_id)
        except Exception as e:
            raise WorkspaceSecurityError(f"無効な会話ID: {str(e)}")

        # 設定ファイルからベースディレクトリを取得
        base_dir = settings.workspace_temp_dir
        workspace_path = Path(base_dir) / f"workspace_{conversation_id}"

        # ベースディレクトリが存在しない場合は作成
        Path(base_dir).mkdir(parents=True, exist_ok=True, mode=0o700)

        return str(workspace_path)

    async def sync_to_local(self, tenant_id: str, conversation_id: str) -> str:
        """
        S3からローカルに同期

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Returns:
            ローカルディレクトリパス

        Raises:
            WorkspaceSecurityError: 同期に失敗した場合
        """
        local_dir = self.get_workspace_local_path(conversation_id)

        # ディレクトリを作成
        Path(local_dir).mkdir(parents=True, exist_ok=True)

        try:
            await self.s3.sync_to_local(tenant_id, conversation_id, local_dir)
        except FileNotFoundError:
            # ファイルがない場合は正常（新規会話）
            logger.info(
                "S3にファイルがありません（新規会話）",
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
        except Exception as e:
            logger.error(
                "S3→ローカル同期エラー",
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                error=str(e),
                exc_info=True,
            )
            raise WorkspaceSecurityError(
                f"ワークスペース同期に失敗しました: {str(e)}"
            )

        return local_dir

    async def sync_from_local(self, tenant_id: str, conversation_id: str) -> list[str]:
        """
        ローカルからS3に同期

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Returns:
            同期されたファイルパスのリスト

        Raises:
            WorkspaceSecurityError: 同期に失敗した場合
        """
        local_dir = self.get_workspace_local_path(conversation_id)

        # ローカルディレクトリが存在しない場合は空リストを返す
        if not Path(local_dir).exists():
            logger.info(
                "ローカルディレクトリが存在しません",
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                local_dir=local_dir,
            )
            return []

        try:
            return await self.s3.sync_from_local(tenant_id, conversation_id, local_dir)
        except Exception as e:
            logger.error(
                "ローカル→S3同期エラー",
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                error=str(e),
                exc_info=True,
            )
            raise WorkspaceSecurityError(
                f"ワークスペース同期に失敗しました: {str(e)}"
            )

    async def cleanup_local(self, conversation_id: str) -> None:
        """
        ローカルの一時ファイルを削除

        Args:
            conversation_id: 会話ID
        """
        local_dir = self.get_workspace_local_path(conversation_id)

        if Path(local_dir).exists():
            # Python 3.12+では onerror は非推奨、onexc を使用
            if sys.version_info >= (3, 12):
                def log_error_onexc(func, path, exc):
                    """削除エラー時のコールバック（Python 3.12+用）"""
                    logger.warning(
                        "ローカルワークスペース削除エラー",
                        conversation_id=conversation_id,
                        path=path,
                        error=str(exc),
                    )
                shutil.rmtree(local_dir, onexc=log_error_onexc)
            else:
                def log_error_onerror(func, path, exc_info):
                    """削除エラー時のコールバック（Python 3.11以前用）"""
                    logger.warning(
                        "ローカルワークスペース削除エラー",
                        conversation_id=conversation_id,
                        path=path,
                        error=str(exc_info[1]) if exc_info else "Unknown error",
                    )
                shutil.rmtree(local_dir, onerror=log_error_onerror)
            logger.info("ローカルワークスペース削除完了", conversation_id=conversation_id)
        else:
            logger.debug("ローカルワークスペースは存在しません", conversation_id=conversation_id)

    def get_workspace_cwd(self, tenant_id: str, conversation_id: str) -> str:
        """
        会話専用ワークスペースのcwd（作業ディレクトリ）を取得

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Returns:
            cwdパス（ローカル一時ディレクトリ）
        """
        return self.get_workspace_local_path(conversation_id)

    async def get_workspace_info(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> Optional[WorkspaceInfo]:
        """
        ワークスペース情報を取得

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Returns:
            ワークスペース情報（存在しない場合はNone）
        """
        # 会話取得
        result = await self.db.execute(
            select(Conversation).where(
                and_(
                    Conversation.conversation_id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                )
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation or not conversation.workspace_enabled:
            return None

        # ファイル統計を取得
        file_count, total_size = await self._get_file_stats(conversation_id)

        return WorkspaceInfo(
            conversation_id=conversation_id,
            workspace_enabled=conversation.workspace_enabled,
            workspace_path=conversation.workspace_path,
            workspace_created_at=conversation.workspace_created_at,
            file_count=file_count,
            total_size=total_size,
        )

    async def enable_workspace(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> None:
        """
        ワークスペースを有効化

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
        """
        now = datetime.now(timezone.utc)
        local_path = self.get_workspace_local_path(conversation_id)

        await self.db.execute(
            update(Conversation)
            .where(
                and_(
                    Conversation.conversation_id == conversation_id,
                    Conversation.tenant_id == tenant_id,
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
            conversation_id=conversation_id,
        )

    async def get_context_for_ai(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> Optional[WorkspaceContextForAI]:
        """
        AIに提供するワークスペースコンテキストを生成

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Returns:
            AIコンテキスト（ワークスペースが無効な場合はNone）
        """
        workspace_info = await self.get_workspace_info(tenant_id, conversation_id)
        if not workspace_info or not workspace_info.workspace_enabled:
            return None

        file_list = await self.list_files(tenant_id, conversation_id)

        return self.context_builder.build_context(workspace_info, file_list)

    async def _save_file_record(
        self,
        tenant_id: str,
        conversation_id: str,
        file_path: str,
        original_name: str,
        file_size: int,
        content_type: str,
        source: str,
        is_presented: bool = False,
        original_relative_path: Optional[str] = None,
    ) -> ConversationFileInfo:
        """
        ファイルレコードをDBに保存

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
            file_path: ファイルパス
            original_name: 元のファイル名
            file_size: ファイルサイズ
            content_type: MIMEタイプ
            source: ソース
            is_presented: Presentedフラグ
            original_relative_path: 元の相対パス（表示用）

        Returns:
            ファイル情報
        """
        # バージョン取得
        new_version = await self._get_next_version(conversation_id, file_path)

        # DBに記録
        conversation_file = ConversationFile(
            file_id=str(uuid4()),
            conversation_id=conversation_id,
            file_path=file_path,
            original_name=original_name,
            file_size=file_size,
            mime_type=content_type,
            version=new_version,
            source=source,
            is_presented=is_presented,
            checksum=None,  # S3版ではチェックサムは省略
            description=None,
            original_relative_path=original_relative_path,
            status="active",
        )
        self.db.add(conversation_file)
        await self.db.flush()
        await self.db.refresh(conversation_file)

        logger.info(
            "ファイルレコード保存完了",
            conversation_id=conversation_id,
            file_path=file_path,
            version=new_version,
        )

        return self._to_file_info(conversation_file)

    async def _get_file_records(
        self,
        conversation_id: str,
        is_presented: Optional[bool] = None,
    ) -> list[ConversationFileInfo]:
        """
        ファイルレコードを取得

        Args:
            conversation_id: 会話ID
            is_presented: Presentedフラグでフィルタ

        Returns:
            ファイル情報のリスト
        """
        conditions = [
            ConversationFile.conversation_id == conversation_id,
            ConversationFile.status == "active",
        ]

        if is_presented is not None:
            conditions.append(ConversationFile.is_presented == is_presented)

        # 最新バージョンのみ取得
        subquery = (
            select(
                ConversationFile.file_path,
                func.max(ConversationFile.version).label("max_version"),
            )
            .where(and_(*conditions))
            .group_by(ConversationFile.file_path)
            .subquery()
        )

        query = (
            select(ConversationFile)
            .join(
                subquery,
                and_(
                    ConversationFile.file_path == subquery.c.file_path,
                    ConversationFile.version == subquery.c.max_version,
                ),
            )
            .where(and_(*conditions))
            .order_by(ConversationFile.created_at.desc())
        )

        result = await self.db.execute(query)
        files = result.scalars().all()

        return [self._to_file_info(f) for f in files]

    async def _get_file_stats(
        self,
        conversation_id: str,
    ) -> tuple[int, int]:
        """
        ファイル統計を取得

        Args:
            conversation_id: 会話ID

        Returns:
            (ファイル数, 合計サイズ)
        """
        stats = await self.db.execute(
            select(
                func.count(ConversationFile.file_id).label("file_count"),
                func.coalesce(func.sum(ConversationFile.file_size), 0).label("total_size"),
            ).where(
                and_(
                    ConversationFile.conversation_id == conversation_id,
                    ConversationFile.status == "active",
                )
            )
        )
        row = stats.first()
        return (row.file_count if row else 0, row.total_size if row else 0)

    async def _get_next_version(
        self,
        conversation_id: str,
        file_path: str,
    ) -> int:
        """
        次のバージョン番号を取得

        Args:
            conversation_id: 会話ID
            file_path: ファイルパス

        Returns:
            次のバージョン番号
        """
        result = await self.db.execute(
            select(func.max(ConversationFile.version)).where(
                and_(
                    ConversationFile.conversation_id == conversation_id,
                    ConversationFile.file_path == file_path,
                    ConversationFile.status == "active",
                )
            )
        )
        max_version = result.scalar()
        return (max_version + 1) if max_version else 1

    async def _verify_conversation_ownership(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> None:
        """
        会話所有権を確認

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID

        Raises:
            WorkspaceSecurityError: アクセス拒否
        """
        conversation_result = await self.db.execute(
            select(Conversation).where(
                and_(
                    Conversation.conversation_id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                )
            )
        )
        if not conversation_result.scalar_one_or_none():
            raise WorkspaceSecurityError("会話へのアクセスが拒否されました")

    def _to_file_info(self, conversation_file: ConversationFile) -> ConversationFileInfo:
        """
        ConversationFileをConversationFileInfoに変換

        Args:
            conversation_file: ConversationFileモデル

        Returns:
            ConversationFileInfo
        """
        return ConversationFileInfo(
            file_id=conversation_file.file_id,
            file_path=conversation_file.file_path,
            original_name=conversation_file.original_name,
            original_relative_path=conversation_file.original_relative_path,
            file_size=conversation_file.file_size,
            mime_type=conversation_file.mime_type,
            version=conversation_file.version,
            source=conversation_file.source,
            is_presented=conversation_file.is_presented,
            checksum=conversation_file.checksum,
            description=conversation_file.description,
            created_at=conversation_file.created_at,
            updated_at=conversation_file.updated_at,
        )
