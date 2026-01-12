"""
ファイルマネージャー
ワークスペースのファイル操作を担当
"""
import hashlib
import mimetypes
from pathlib import Path
from typing import Optional
from uuid import uuid4

import structlog
from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session_file import SessionFile
from app.schemas.workspace import SessionFileInfo, WorkspaceFileList
from app.services.workspace.path_validator import PathValidator
from app.utils.exceptions import WorkspaceSecurityError, FileSizeError

logger = structlog.get_logger(__name__)

# ワークスペースの設定
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_TOTAL_WORKSPACE_SIZE = 500 * 1024 * 1024  # 500MB per session
ALLOWED_EXTENSIONS = {
    # テキストファイル
    ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".csv", ".tsv",
    ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx",
    # プログラミング言語
    ".py", ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb",
    ".php", ".swift", ".kt", ".scala", ".r", ".sql", ".sh", ".bash",
    # ドキュメント
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # 画像
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    # アーカイブ
    ".zip", ".tar", ".gz", ".7z",
    # その他
    ".log", ".ini", ".conf", ".cfg", ".env", ".toml",
}


class FileManager:
    """
    ファイルマネージャー

    ファイルのアップロード、ダウンロード、一覧取得を担当
    """

    def __init__(self, db: AsyncSession, path_validator: PathValidator):
        """
        初期化

        Args:
            db: データベースセッション
            path_validator: パスバリデーター
        """
        self.db = db
        self.path_validator = path_validator

    async def upload_file(
        self,
        tenant_id: str,
        chat_session_id: str,
        file_path: str,
        content: bytes,
        original_name: str,
        description: Optional[str] = None,
        current_total_size: int = 0,
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
            current_total_size: 現在のワークスペース合計サイズ

        Returns:
            アップロードされたファイル情報
        """
        # ファイルパスの検証
        validated_path = self.path_validator.validate_file_path(file_path)

        workspace_root = self.path_validator.get_workspace_root(
            tenant_id, chat_session_id
        )
        full_path = workspace_root / validated_path

        # セキュリティ検証
        self.path_validator.validate_path(workspace_root, full_path)

        # ファイルサイズ検証
        file_size = len(content)
        if file_size > MAX_FILE_SIZE:
            raise FileSizeError(file_size, MAX_FILE_SIZE)

        # 合計サイズ検証
        if current_total_size + file_size > MAX_TOTAL_WORKSPACE_SIZE:
            raise WorkspaceSecurityError(
                f"ワークスペースサイズが上限（{MAX_TOTAL_WORKSPACE_SIZE // (1024*1024)}MB）を超えています"
            )

        # 拡張子検証（警告のみ）
        ext = Path(original_name).suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            logger.warning(
                "許可されていない拡張子",
                extension=ext,
                allowed=list(ALLOWED_EXTENSIONS),
            )

        # バージョン管理
        new_version = await self._get_next_version(chat_session_id, validated_path)

        # ディレクトリ作成
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # ファイル書き込み
        with open(full_path, "wb") as f:
            f.write(content)

        # チェックサム計算
        checksum = hashlib.sha256(content).hexdigest()

        # MIMEタイプ推測
        mime_type, _ = mimetypes.guess_type(original_name)

        # DBに記録
        session_file = SessionFile(
            file_id=str(uuid4()),
            chat_session_id=chat_session_id,
            file_path=validated_path,
            original_name=original_name,
            file_size=file_size,
            mime_type=mime_type,
            version=new_version,
            source="user_upload",
            is_presented=False,
            checksum=checksum,
            description=description,
            status="active",
        )
        self.db.add(session_file)
        await self.db.flush()
        await self.db.refresh(session_file)

        logger.info(
            "ファイルアップロード完了",
            chat_session_id=chat_session_id,
            file_path=validated_path,
            version=new_version,
            file_size=file_size,
        )

        return self._to_file_info(session_file)

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
        validated_path = self.path_validator.validate_file_path(file_path)
        workspace_root = self.path_validator.get_workspace_root(
            tenant_id, chat_session_id
        )
        full_path = workspace_root / validated_path

        # セキュリティ検証
        self.path_validator.validate_path(workspace_root, full_path)

        # ファイルの存在確認
        if not full_path.exists():
            logger.warning(
                "AI登録対象ファイルが存在しません",
                file_path=validated_path,
            )
            return None

        # ファイル情報取得
        stat = full_path.stat()
        file_size = stat.st_size

        # チェックサム計算
        with open(full_path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        # MIMEタイプ推測
        mime_type, _ = mimetypes.guess_type(full_path.name)

        # 既存ファイルのチェック
        existing_file = await self._get_latest_file(chat_session_id, validated_path)

        if existing_file:
            # チェックサムが同じ場合はスキップ
            if existing_file.checksum == checksum:
                logger.debug("ファイル内容に変更なし", file_path=validated_path)
                if is_presented and not existing_file.is_presented:
                    existing_file.is_presented = True
                    await self.db.flush()
                return self._to_file_info(existing_file)

        new_version = await self._get_next_version(chat_session_id, validated_path)

        # DBに記録
        session_file = SessionFile(
            file_id=str(uuid4()),
            chat_session_id=chat_session_id,
            file_path=validated_path,
            original_name=full_path.name,
            file_size=file_size,
            mime_type=mime_type,
            version=new_version,
            source=source,
            is_presented=is_presented,
            checksum=checksum,
            description=description,
            status="active",
        )
        self.db.add(session_file)
        await self.db.flush()
        await self.db.refresh(session_file)

        logger.info(
            "AIファイル登録完了",
            chat_session_id=chat_session_id,
            file_path=validated_path,
            version=new_version,
            source=source,
            is_presented=is_presented,
        )

        return self._to_file_info(session_file)

    async def list_files(
        self,
        chat_session_id: str,
        include_all_versions: bool = False,
    ) -> WorkspaceFileList:
        """
        ワークスペース内のファイル一覧を取得

        Args:
            chat_session_id: チャットセッションID
            include_all_versions: 全バージョンを含めるか

        Returns:
            ファイル一覧
        """
        if include_all_versions:
            query = (
                select(SessionFile)
                .where(
                    and_(
                        SessionFile.chat_session_id == chat_session_id,
                        SessionFile.status == "active",
                    )
                )
                .order_by(SessionFile.file_path, SessionFile.version.desc())
            )
        else:
            # 最新バージョンのみ取得
            subquery = (
                select(
                    SessionFile.file_path,
                    func.max(SessionFile.version).label("max_version"),
                )
                .where(
                    and_(
                        SessionFile.chat_session_id == chat_session_id,
                        SessionFile.status == "active",
                    )
                )
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
                .where(
                    and_(
                        SessionFile.chat_session_id == chat_session_id,
                        SessionFile.status == "active",
                    )
                )
                .order_by(SessionFile.file_path)
            )

        result = await self.db.execute(query)
        files = result.scalars().all()

        file_infos = [self._to_file_info(f) for f in files]
        total_size = sum(f.file_size for f in file_infos)

        return WorkspaceFileList(
            chat_session_id=chat_session_id,
            files=file_infos,
            total_count=len(file_infos),
            total_size=total_size,
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
        validated_path = self.path_validator.validate_file_path(file_path)

        # ファイルレコード取得
        session_file = await self._get_file_by_version(
            chat_session_id, validated_path, version
        )

        if not session_file:
            raise WorkspaceSecurityError("ファイルが見つかりません")

        # ファイル読み込み
        workspace_root = self.path_validator.get_workspace_root(
            tenant_id, chat_session_id
        )
        full_path = workspace_root / validated_path

        # 最終セキュリティ検証
        self.path_validator.validate_path(workspace_root, full_path)

        if not full_path.exists():
            raise WorkspaceSecurityError("ファイルが見つかりません")

        with open(full_path, "rb") as f:
            content = f.read()

        mime_type = session_file.mime_type or "application/octet-stream"

        return content, session_file.original_name, mime_type

    async def set_presented(
        self,
        chat_session_id: str,
        file_path: str,
        description: Optional[str] = None,
    ) -> Optional[SessionFileInfo]:
        """
        ファイルをPresentedとしてマーク

        Args:
            chat_session_id: チャットセッションID
            file_path: ファイルパス
            description: 説明（更新する場合）

        Returns:
            更新されたファイル情報
        """
        validated_path = self.path_validator.validate_file_path(file_path)

        session_file = await self._get_latest_file(chat_session_id, validated_path)

        if not session_file:
            return None

        session_file.is_presented = True
        if description:
            session_file.description = description

        await self.db.flush()
        await self.db.refresh(session_file)

        return self._to_file_info(session_file)

    async def get_presented_files(
        self,
        chat_session_id: str,
    ) -> list[SessionFileInfo]:
        """
        Presentedファイル一覧を取得

        Args:
            chat_session_id: チャットセッションID

        Returns:
            Presentedファイル一覧
        """
        result = await self.db.execute(
            select(SessionFile).where(
                and_(
                    SessionFile.chat_session_id == chat_session_id,
                    SessionFile.is_presented == True,
                    SessionFile.status == "active",
                )
            ).order_by(SessionFile.created_at.desc())
        )
        files = result.scalars().all()

        return [self._to_file_info(f) for f in files]

    async def get_file_stats(
        self,
        chat_session_id: str,
    ) -> tuple[int, int]:
        """
        ファイル統計を取得

        Args:
            chat_session_id: チャットセッションID

        Returns:
            (ファイル数, 合計サイズ)
        """
        stats = await self.db.execute(
            select(
                func.count(SessionFile.file_id).label("file_count"),
                func.coalesce(func.sum(SessionFile.file_size), 0).label("total_size"),
            ).where(
                and_(
                    SessionFile.chat_session_id == chat_session_id,
                    SessionFile.status == "active",
                )
            )
        )
        row = stats.first()
        return (row.file_count if row else 0, row.total_size if row else 0)

    async def _get_next_version(
        self,
        chat_session_id: str,
        file_path: str,
    ) -> int:
        """次のバージョン番号を取得"""
        existing = await self._get_latest_file(chat_session_id, file_path)
        return (existing.version + 1) if existing else 1

    async def _get_latest_file(
        self,
        chat_session_id: str,
        file_path: str,
    ) -> Optional[SessionFile]:
        """最新バージョンのファイルを取得"""
        result = await self.db.execute(
            select(SessionFile).where(
                and_(
                    SessionFile.chat_session_id == chat_session_id,
                    SessionFile.file_path == file_path,
                    SessionFile.status == "active",
                )
            ).order_by(SessionFile.version.desc())
        )
        return result.scalar_one_or_none()

    async def _get_file_by_version(
        self,
        chat_session_id: str,
        file_path: str,
        version: Optional[int] = None,
    ) -> Optional[SessionFile]:
        """特定バージョンのファイルを取得"""
        query = select(SessionFile).where(
            and_(
                SessionFile.chat_session_id == chat_session_id,
                SessionFile.file_path == file_path,
                SessionFile.status == "active",
            )
        )

        if version:
            query = query.where(SessionFile.version == version)
        else:
            query = query.order_by(SessionFile.version.desc()).limit(1)

        result = await self.db.execute(query)
        return result.scalars().first()

    def _to_file_info(self, session_file: SessionFile) -> SessionFileInfo:
        """SessionFileをSessionFileInfoに変換"""
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
