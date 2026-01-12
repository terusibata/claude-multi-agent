"""
パスバリデーター
ワークスペースのセキュリティ検証を担当
"""
import os
from pathlib import Path

import structlog

from app.utils.exceptions import WorkspaceSecurityError, PathTraversalError

logger = structlog.get_logger(__name__)


class PathValidator:
    """
    パスバリデーター

    セキュリティ原則：
    1. すべてのパスは正規化後に検証
    2. ワークスペースルート外へのアクセスは絶対禁止
    3. テナントIDとセッションIDの両方で検証
    """

    # 危険なパターン
    DANGEROUS_PATTERNS = ["..", "/", "\\", "\x00"]

    def __init__(self, base_path: Path):
        """
        初期化

        Args:
            base_path: スキルベースパス
        """
        self.base_path = base_path

    def get_workspace_root(self, tenant_id: str, chat_session_id: str) -> Path:
        """
        セッション専用ワークスペースのルートパスを取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            ワークスペースルートパス
        """
        self.validate_id(tenant_id, "tenant_id")
        self.validate_id(chat_session_id, "chat_session_id")

        return self.base_path / f"tenant_{tenant_id}" / "workspaces" / chat_session_id

    def validate_id(self, id_value: str, id_name: str) -> None:
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

        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in id_value:
                logger.warning(
                    "セキュリティ警告: 不正なIDパターン検出",
                    id_name=id_name,
                    pattern=pattern,
                )
                raise WorkspaceSecurityError(f"不正な{id_name}です")

    def validate_path(
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
        try:
            workspace_root_resolved = workspace_root.resolve()

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
            raise PathTraversalError(str(target_path))

        return target_resolved

    def validate_file_path(self, file_path: str) -> str:
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

    def get_workspace_cwd(self, tenant_id: str, chat_session_id: str) -> str:
        """
        セッション専用ワークスペースのcwd（作業ディレクトリ）を取得

        Args:
            tenant_id: テナントID
            chat_session_id: チャットセッションID

        Returns:
            cwdパス
        """
        workspace_root = self.get_workspace_root(tenant_id, chat_session_id)
        return str(workspace_root)
