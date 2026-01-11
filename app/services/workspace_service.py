"""
ワークスペースサービス
セッション専用ワークスペースの管理

セキュリティ要件：
- セッション専用ワークスペース以外へのアクセスを絶対に禁止
- パストラバーサル攻撃の防止
- テナント間のアイソレーション
"""
import hashlib
import mimetypes
import os
import shutil
import structlog
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

from sqlalchemy import and_, select, func, update
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

settings = get_settings()
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


class WorkspaceSecurityError(Exception):
    """ワークスペースセキュリティエラー"""
    pass


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

    def _get_workspace_root(self, tenant_id: str, chat_session_id: str) -> Path:
        """
        セッション専用ワークスペースのルートパスを取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            ワークスペースルートパス
        """
        # セキュリティ：IDに不正な文字が含まれていないか検証
        self._validate_id(tenant_id, "tenant_id")
        self._validate_id(chat_session_id, "chat_session_id")

        return self.base_path / f"tenant_{tenant_id}" / "workspaces" / chat_session_id

    def _validate_id(self, id_value: str, id_name: str) -> None:
        """
        IDの検証（セキュリティ）

        Args:
            id_value: 検証するID
            id_name: IDの名前（エラーメッセージ用）

        Raises:
            WorkspaceSecurityError: 不正なID
        """
        if not id_value:
            raise WorkspaceSecurityError(f"{id_name}が空です")

        # パストラバーサル攻撃のパターンを検出
        dangerous_patterns = ["..", "/", "\\", "\x00"]
        for pattern in dangerous_patterns:
            if pattern in id_value:
                logger.warning(
                    "セキュリティ警告: 不正なIDパターン検出",
                    id_name=id_name,
                    pattern=pattern,
                )
                raise WorkspaceSecurityError(f"不正な{id_name}です")

    def _validate_path(
        self,
        workspace_root: Path,
        target_path: Path,
    ) -> Path:
        """
        パスの検証（セキュリティ最重要）

        Args:
            workspace_root: ワークスペースルート
            target_path: 検証対象のパス

        Returns:
            検証済みの正規化されたパス

        Raises:
            WorkspaceSecurityError: パストラバーサル攻撃検出
        """
        # パスを正規化（シンボリックリンクも解決）
        try:
            # ワークスペースルートを正規化
            workspace_root_resolved = workspace_root.resolve()

            # ターゲットパスを正規化
            # ファイルが存在しない場合もあるため、親ディレクトリで検証
            if target_path.exists():
                target_resolved = target_path.resolve()
            else:
                # 親ディレクトリが存在するか確認
                parent = target_path.parent
                while not parent.exists() and parent != parent.parent:
                    parent = parent.parent
                if parent.exists():
                    target_resolved = parent.resolve() / target_path.relative_to(parent)
                else:
                    target_resolved = workspace_root_resolved / target_path.name

        except (ValueError, RuntimeError) as e:
            logger.warning(
                "セキュリティ警告: パス正規化エラー",
                error=str(e),
                target_path=str(target_path),
            )
            raise WorkspaceSecurityError("パスの検証に失敗しました")

        # 絶対パスで比較してワークスペース外へのアクセスを検出
        try:
            target_resolved.relative_to(workspace_root_resolved)
        except ValueError:
            logger.warning(
                "セキュリティ警告: パストラバーサル攻撃検出",
                workspace_root=str(workspace_root_resolved),
                target_path=str(target_resolved),
            )
            raise WorkspaceSecurityError("ワークスペース外へのアクセスは許可されていません")

        return target_resolved

    def _validate_file_path(self, file_path: str) -> str:
        """
        ファイルパス文字列の検証

        Args:
            file_path: 検証するファイルパス

        Returns:
            正規化されたファイルパス

        Raises:
            WorkspaceSecurityError: 不正なパス
        """
        if not file_path:
            raise WorkspaceSecurityError("ファイルパスが空です")

        # NULLバイト攻撃の防止
        if "\x00" in file_path:
            raise WorkspaceSecurityError("不正なファイルパスです")

        # 絶対パスは拒否
        if file_path.startswith("/") or (len(file_path) > 1 and file_path[1] == ":"):
            raise WorkspaceSecurityError("絶対パスは使用できません")

        # パストラバーサルパターンを検出
        normalized = os.path.normpath(file_path)
        if normalized.startswith(".."):
            raise WorkspaceSecurityError("親ディレクトリへのアクセスは許可されていません")

        return normalized

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
        workspace_root = self._get_workspace_root(tenant_id, chat_session_id)

        # ディレクトリ作成
        workspace_root.mkdir(parents=True, exist_ok=True)

        # サブディレクトリを作成
        (workspace_root / "uploads").mkdir(exist_ok=True)  # ユーザーアップロード
        (workspace_root / "outputs").mkdir(exist_ok=True)  # AI生成ファイル
        (workspace_root / "temp").mkdir(exist_ok=True)     # 一時ファイル

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

        return WorkspaceInfo(
            chat_session_id=chat_session_id,
            workspace_enabled=session.workspace_enabled,
            workspace_path=session.workspace_path,
            workspace_created_at=session.workspace_created_at,
            file_count=row.file_count if row else 0,
            total_size=row.total_size if row else 0,
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
        # ファイルパスの検証
        validated_path = self._validate_file_path(file_path)

        # ワークスペースの存在確認と作成
        workspace_info = await self.get_workspace_info(tenant_id, chat_session_id)
        if not workspace_info:
            await self.create_workspace(tenant_id, chat_session_id)

        workspace_root = self._get_workspace_root(tenant_id, chat_session_id)
        full_path = workspace_root / validated_path

        # セキュリティ検証
        self._validate_path(workspace_root, full_path)

        # ファイルサイズ検証
        file_size = len(content)
        if file_size > MAX_FILE_SIZE:
            raise WorkspaceSecurityError(
                f"ファイルサイズが上限（{MAX_FILE_SIZE // (1024*1024)}MB）を超えています"
            )

        # 合計サイズ検証
        current_info = await self.get_workspace_info(tenant_id, chat_session_id)
        if current_info and current_info.total_size + file_size > MAX_TOTAL_WORKSPACE_SIZE:
            raise WorkspaceSecurityError(
                f"ワークスペースサイズが上限（{MAX_TOTAL_WORKSPACE_SIZE // (1024*1024)}MB）を超えています"
            )

        # 拡張子検証
        ext = Path(original_name).suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            logger.warning(
                "許可されていない拡張子",
                extension=ext,
                allowed=list(ALLOWED_EXTENSIONS),
            )
            # 警告のみ、拒否はしない（柔軟性のため）

        # バージョン管理：既存ファイルのバージョンを取得
        existing = await self.db.execute(
            select(SessionFile).where(
                and_(
                    SessionFile.chat_session_id == chat_session_id,
                    SessionFile.file_path == validated_path,
                    SessionFile.status == "active",
                )
            ).order_by(SessionFile.version.desc())
        )
        existing_file = existing.scalar_one_or_none()

        new_version = 1
        if existing_file:
            new_version = existing_file.version + 1
            # 古いバージョンは保持（バージョン管理）

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
        validated_path = self._validate_file_path(file_path)
        workspace_root = self._get_workspace_root(tenant_id, chat_session_id)
        full_path = workspace_root / validated_path

        # セキュリティ検証
        self._validate_path(workspace_root, full_path)

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

        # バージョン管理
        existing = await self.db.execute(
            select(SessionFile).where(
                and_(
                    SessionFile.chat_session_id == chat_session_id,
                    SessionFile.file_path == validated_path,
                    SessionFile.status == "active",
                )
            ).order_by(SessionFile.version.desc())
        )
        existing_file = existing.scalar_one_or_none()

        new_version = 1
        if existing_file:
            # チェックサムが同じ場合はスキップ
            if existing_file.checksum == checksum:
                logger.debug("ファイル内容に変更なし", file_path=validated_path)
                # is_presentedフラグの更新のみ
                if is_presented and not existing_file.is_presented:
                    existing_file.is_presented = True
                    await self.db.flush()
                return SessionFileInfo(
                    file_id=existing_file.file_id,
                    file_path=existing_file.file_path,
                    original_name=existing_file.original_name,
                    file_size=existing_file.file_size,
                    mime_type=existing_file.mime_type,
                    version=existing_file.version,
                    source=existing_file.source,
                    is_presented=existing_file.is_presented,
                    checksum=existing_file.checksum,
                    description=existing_file.description,
                    created_at=existing_file.created_at,
                    updated_at=existing_file.updated_at,
                )
            new_version = existing_file.version + 1

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
        self._validate_id(tenant_id, "tenant_id")
        self._validate_id(chat_session_id, "chat_session_id")

        # セッション所有権確認
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

        # ファイル取得
        query = select(SessionFile).where(
            and_(
                SessionFile.chat_session_id == chat_session_id,
                SessionFile.status == "active",
            )
        )

        if not include_all_versions:
            # 最新バージョンのみ取得するサブクエリ
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
            )

        query = query.order_by(SessionFile.file_path, SessionFile.version.desc())
        result = await self.db.execute(query)
        files = result.scalars().all()

        file_infos = [
            SessionFileInfo(
                file_id=f.file_id,
                file_path=f.file_path,
                original_name=f.original_name,
                file_size=f.file_size,
                mime_type=f.mime_type,
                version=f.version,
                source=f.source,
                is_presented=f.is_presented,
                checksum=f.checksum,
                description=f.description,
                created_at=f.created_at,
                updated_at=f.updated_at,
            )
            for f in files
        ]

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
        # セキュリティ検証
        validated_path = self._validate_file_path(file_path)

        # セッション所有権確認
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

        # ファイルレコード取得
        query = select(SessionFile).where(
            and_(
                SessionFile.chat_session_id == chat_session_id,
                SessionFile.file_path == validated_path,
                SessionFile.status == "active",
            )
        )

        if version:
            query = query.where(SessionFile.version == version)
        else:
            # バージョン指定なしの場合は最新バージョンを取得
            query = query.order_by(SessionFile.version.desc()).limit(1)

        result = await self.db.execute(query)
        session_file = result.scalars().first()

        if not session_file:
            raise WorkspaceSecurityError("ファイルが見つかりません")

        # ファイル読み込み
        workspace_root = self._get_workspace_root(tenant_id, chat_session_id)
        full_path = workspace_root / validated_path

        # 最終セキュリティ検証
        self._validate_path(workspace_root, full_path)

        if not full_path.exists():
            raise WorkspaceSecurityError("ファイルが見つかりません")

        with open(full_path, "rb") as f:
            content = f.read()

        mime_type = session_file.mime_type or "application/octet-stream"

        return content, session_file.original_name, mime_type

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
        validated_path = self._validate_file_path(file_path)

        # 最新バージョンを取得
        result = await self.db.execute(
            select(SessionFile).where(
                and_(
                    SessionFile.chat_session_id == chat_session_id,
                    SessionFile.file_path == validated_path,
                    SessionFile.status == "active",
                )
            ).order_by(SessionFile.version.desc())
        )
        session_file = result.scalar_one_or_none()

        if not session_file:
            return None

        session_file.is_presented = True
        if description:
            session_file.description = description

        await self.db.flush()
        await self.db.refresh(session_file)

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

        return [
            SessionFileInfo(
                file_id=f.file_id,
                file_path=f.file_path,
                original_name=f.original_name,
                file_size=f.file_size,
                mime_type=f.mime_type,
                version=f.version,
                source=f.source,
                is_presented=f.is_presented,
                checksum=f.checksum,
                description=f.description,
                created_at=f.created_at,
                updated_at=f.updated_at,
            )
            for f in files
        ]

    def get_workspace_cwd(self, tenant_id: str, chat_session_id: str) -> str:
        """
        セッション専用ワークスペースのcwd（作業ディレクトリ）を取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            cwdパス
        """
        workspace_root = self._get_workspace_root(tenant_id, chat_session_id)
        return str(workspace_root)

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

        files = [
            {
                "path": f.file_path,
                "size": f.file_size,
                "type": f.mime_type or "unknown",
                "source": f.source,
                "description": f.description or "",
            }
            for f in file_list.files
        ]

        instructions = f"""
## ワークスペース情報

あなたはセッション専用ワークスペースで作業しています。

### 利用可能なファイル:
{self._format_file_list(files)}

### ガイドライン:
1. ファイルの読み取り: Readツールでワークスペース内のファイルを読み取れます
2. ファイルの作成/編集: Writeツールでファイルを作成・編集できます
3. コマンド実行: Bashツールでコマンドを実行できます（カレントディレクトリはワークスペース）
4. ファイル検索: Glob/Grepツールでファイルを検索できます

### 重要なセキュリティ制限:
- ワークスペース外のファイルにはアクセスできません
- 絶対パスは使用せず、相対パスを使用してください
- 親ディレクトリ（..）へのアクセスは禁止されています

### ファイル作成時の重要な注意:
ファイルを作成した場合は、以下のように返答してください:
- 「ファイル 'xxx.py' を作成しました。上記からダウンロードできます。」
- 「python xxx.py で実行できます」のような実行方法の案内は不要です
- ユーザーはこの環境でコマンドを実行できません。代わりにダウンロードして利用します

ファイル作成時は、システムが自動的にファイルをユーザーに提示します。
あなたが直接コマンドライン実行を勧める必要はありません。
"""

        return WorkspaceContextForAI(
            workspace_path=workspace_info.workspace_path,
            files=files,
            instructions=instructions,
        )

    def _format_file_list(self, files: list[dict]) -> str:
        """ファイルリストをテキスト形式にフォーマット"""
        if not files:
            return "（ファイルなし）"

        lines = []
        for f in files:
            size_str = self._format_size(f["size"])
            source = "📤" if f["source"] == "user_upload" else "🤖"
            desc = f" - {f['description']}" if f.get("description") else ""
            lines.append(f"  {source} {f['path']} ({size_str}){desc}")

        return "\n".join(lines)

    def _format_size(self, size: int) -> str:
        """ファイルサイズを人間が読みやすい形式にフォーマット"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

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
                size = sum(f.stat().st_size for f in workspace_path.rglob("*") if f.is_file())
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
