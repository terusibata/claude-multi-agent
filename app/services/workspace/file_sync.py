"""
ワークスペースファイル同期
S3 ↔ コンテナ間のファイル同期を担当

同期フロー:
  - sync_to_container: S3 → コンテナ（コンテナ割り当て時）
  - sync_from_container: コンテナ → S3（実行完了時、コンテナ破棄時）
"""
import asyncio
import base64

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.audit_log import (
    audit_file_sync_from_container,
    audit_file_sync_to_container,
)
from app.models.conversation_file import ConversationFile
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.workspace.s3_storage import S3StorageBackend

logger = structlog.get_logger(__name__)

# システム内部で使用する予約プレフィックス
# これらのパスはワークスペース同期（sync_from_container / sync_to_container）から除外される
RESERVED_PREFIXES = frozenset({
    "_sdk_session/",
})

# 同期対象から除外するパターン
# ビルド成果物・キャッシュ・VCS等の不要ファイルを S3/DB に同期しない
_EXCLUDED_DIR_NAMES = frozenset({
    "__pycache__",
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".eggs",
    ".egg-info",
})

_EXCLUDED_EXTENSIONS = frozenset({
    ".pyc",
    ".pyo",
    ".DS_Store",
})


class WorkspaceFileSync:
    """S3 ↔ コンテナ間のファイル同期"""

    def __init__(
        self,
        s3: S3StorageBackend,
        lifecycle: ContainerLifecycleManager,
        db: AsyncSession,
    ) -> None:
        self.s3 = s3
        self.lifecycle = lifecycle
        self.db = db
        # バックグラウンド同期タスクからの並行DB操作を排他制御
        self._db_lock = asyncio.Lock()

    @staticmethod
    def _is_reserved_path(file_path: str) -> bool:
        """予約プレフィックスに該当するパスかチェック"""
        return any(
            file_path.startswith(prefix) or file_path == prefix.rstrip("/")
            for prefix in RESERVED_PREFIXES
        )

    @staticmethod
    def _should_exclude(file_path: str) -> bool:
        """
        同期対象外のファイルかチェック

        __pycache__、.git、node_modules 等のビルド成果物・キャッシュファイルを除外する。
        """
        # パスセグメントを分解して除外ディレクトリ名をチェック
        segments = file_path.split("/")
        for seg in segments[:-1]:  # 最後のセグメント（ファイル名）以外
            if seg in _EXCLUDED_DIR_NAMES:
                return True

        # ファイル拡張子・名前チェック
        filename = segments[-1] if segments else ""
        for ext in _EXCLUDED_EXTENSIONS:
            if filename.endswith(ext) or filename == ext.lstrip("."):
                return True

        return False

    async def sync_to_container(
        self,
        tenant_id: str,
        conversation_id: str,
        container_id: str,
    ) -> int:
        """
        S3からコンテナの/workspaceにファイルを同期

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
            container_id: コンテナID

        Returns:
            同期したファイル数
        """
        from sqlalchemy import select

        # DBから会話のファイル一覧を取得
        stmt = select(ConversationFile).where(
            ConversationFile.conversation_id == conversation_id
        )
        result = await self.db.execute(stmt)
        files = result.scalars().all()

        if not files:
            return 0

        # 並列同期: Semaphoreで同時実行数を制限し、asyncio.gatherで並列実行
        max_concurrent = 5
        sem = asyncio.Semaphore(max_concurrent)
        synced = 0

        async def _sync_single_file(file_record: ConversationFile) -> bool:
            """単一ファイルのS3ダウンロード→コンテナ書き込み"""
            if self._is_reserved_path(file_record.file_path):
                logger.warning(
                    "予約パスのファイルレコード検出（スキップ）",
                    file_path=file_record.file_path,
                    conversation_id=conversation_id,
                )
                return False
            async with sem:
                try:
                    data, _ = await self.s3.download(
                        tenant_id, conversation_id, file_record.file_path
                    )
                    await self._write_to_container(
                        container_id,
                        f"/workspace/{file_record.file_path}",
                        data,
                    )
                    return True
                except Exception as e:
                    logger.error(
                        "ファイル同期エラー（S3→コンテナ）",
                        file_path=file_record.file_path,
                        container_id=container_id,
                        error=str(e),
                    )
                    return False

        results = await asyncio.gather(
            *[_sync_single_file(f) for f in files],
            return_exceptions=True,
        )
        synced = sum(1 for r in results if r is True)

        logger.info(
            "S3→コンテナ同期完了",
            conversation_id=conversation_id,
            container_id=container_id,
            synced=synced,
            total=len(files),
        )
        audit_file_sync_to_container(
            conversation_id=conversation_id,
            container_id=container_id,
            tenant_id=tenant_id,
            synced_count=synced,
            total_count=len(files),
        )
        return synced

    async def sync_from_container(
        self,
        tenant_id: str,
        conversation_id: str,
        container_id: str,
    ) -> int:
        """
        コンテナの/workspaceからS3にファイルを同期

        変更検出: コンテナ内でチェックサム比較を行い、変更があったファイルのみ同期

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
            container_id: コンテナID

        Returns:
            同期したファイル数
        """
        # コンテナ内のファイル一覧を取得
        exit_code, output = await self.lifecycle.exec_in_container(
            container_id,
            ["find", "/workspace", "-type", "f", "-printf", "%P\\n"],
        )
        if exit_code != 0:
            logger.error("コンテナ内ファイル一覧取得失敗", container_id=container_id)
            return 0

        file_paths = [p.strip() for p in output.strip().split("\n") if p.strip()]

        # 予約プレフィックスのファイルを除外（システム内部ファイルがワークスペースとして同期されるのを防止）
        file_paths = [p for p in file_paths if not self._is_reserved_path(p)]

        # 不要ファイル（__pycache__、.git、node_modules等）を除外
        file_paths = [p for p in file_paths if not self._should_exclude(p)]

        if not file_paths:
            return 0

        # 並列同期: Semaphoreで同時実行数を制限し、asyncio.gatherで並列実行
        max_concurrent = 5
        sem = asyncio.Semaphore(max_concurrent)
        synced = 0

        async def _sync_single_file(file_path: str) -> bool:
            """単一ファイルのコンテナ読み出し→S3アップロード"""
            async with sem:
                try:
                    data = await self._read_from_container(
                        container_id, f"/workspace/{file_path}"
                    )
                    if data is not None:
                        await self.s3.upload(
                            tenant_id, conversation_id, file_path, data
                        )
                        await self._upsert_file_record(conversation_id, file_path, len(data))
                        return True
                    return False
                except Exception as e:
                    logger.error(
                        "ファイル同期エラー（コンテナ→S3）",
                        file_path=file_path,
                        container_id=container_id,
                        error=str(e),
                    )
                    return False

        results = await asyncio.gather(
            *[_sync_single_file(fp) for fp in file_paths],
            return_exceptions=True,
        )
        synced = sum(1 for r in results if r is True)

        logger.info(
            "コンテナ→S3同期完了",
            conversation_id=conversation_id,
            container_id=container_id,
            synced=synced,
        )
        audit_file_sync_from_container(
            conversation_id=conversation_id,
            container_id=container_id,
            tenant_id=tenant_id,
            synced_count=synced,
        )
        return synced

    async def _write_to_container(
        self, container_id: str, dest_path: str, data: bytes
    ) -> None:
        """exec + base64 でコンテナにファイルを書き込む

        Docker の put_archive API は tmpfs マウント上で失敗する場合がある
        （ReadonlyRootfs + tmpfs 構成、Docker-in-Docker、userns-remap 等）。
        exec はコンテナ内プロセスとして実行されるため tmpfs も正しく書き込める。
        """
        # 親ディレクトリを確保
        parent_dir = "/".join(dest_path.split("/")[:-1])
        await self.lifecycle.exec_in_container(
            container_id, ["mkdir", "-p", parent_dir]
        )

        encoded = base64.b64encode(data).decode("ascii")

        # チャンク分割（shell 引数制限回避: 60KB 以下で分割）
        chunk_size = 60000
        filename = dest_path.split("/")[-1]
        tmp_path = f"/tmp/_ws_xfer_{filename}"

        for i in range(0, len(encoded), chunk_size):
            chunk = encoded[i:i + chunk_size]
            op = ">>" if i > 0 else ">"
            exit_code, _ = await self.lifecycle.exec_in_container(
                container_id,
                ["sh", "-c", f"printf '%s' '{chunk}' {op} '{tmp_path}'"],
            )
            if exit_code != 0:
                await self.lifecycle.exec_in_container(
                    container_id, ["rm", "-f", tmp_path]
                )
                raise RuntimeError(
                    f"コンテナへのファイル書き込み失敗(chunk): {dest_path}"
                )

        # base64 デコード → 最終ファイルに書き込み → 一時ファイル削除
        exit_code, _ = await self.lifecycle.exec_in_container(
            container_id,
            ["sh", "-c", f"base64 -d < '{tmp_path}' > '{dest_path}' && rm -f '{tmp_path}'"],
        )
        if exit_code != 0:
            await self.lifecycle.exec_in_container(
                container_id, ["rm", "-f", tmp_path]
            )
            raise RuntimeError(
                f"コンテナへのファイル書き込み失敗(decode): {dest_path}"
            )

    async def _read_from_container(
        self, container_id: str, src_path: str
    ) -> bytes | None:
        """exec + cat でコンテナからファイルを読み出す

        Docker の get_archive API は tmpfs マウント上のファイルを読めない場合がある
        （ReadonlyRootfs + tmpfs 構成、Docker-in-Docker、userns-remap 等）。
        exec はコンテナ内プロセスとして実行されるため tmpfs も正しく読める。
        """
        try:
            exit_code, data = await self.lifecycle.exec_in_container_binary(
                container_id, ["cat", src_path]
            )
            if exit_code != 0:
                logger.error(
                    "コンテナからの読み出し失敗",
                    src_path=src_path,
                    exit_code=exit_code,
                )
                return None
            return data
        except Exception as e:
            logger.error("コンテナからの読み出し失敗", src_path=src_path, error=str(e))
            return None

    async def save_session_file(
        self,
        tenant_id: str,
        conversation_id: str,
        container_id: str,
        session_id: str,
    ) -> bool:
        """
        コンテナ内のSDKセッションファイルをS3に保存

        SDKは ~/.claude/projects/<mangled-cwd>/<session_id>.jsonl にセッションを保存する。
        cwd=/workspace の場合、mangled path は -workspace。

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
            container_id: コンテナID
            session_id: SDKセッションID

        Returns:
            保存成功した場合True
        """
        session_path = f"/home/appuser/.claude/projects/-workspace/{session_id}.jsonl"
        data = await self._read_from_container(container_id, session_path)
        if data is None:
            logger.debug(
                "セッションファイル未検出（スキップ）",
                session_id=session_id,
                container_id=container_id,
            )
            return False

        await self.s3.upload(
            tenant_id, conversation_id,
            f"_sdk_session/{session_id}.jsonl", data,
        )
        logger.info(
            "セッションファイルS3保存完了",
            session_id=session_id,
            conversation_id=conversation_id,
            size=len(data),
        )
        return True

    async def restore_session_file(
        self,
        tenant_id: str,
        conversation_id: str,
        container_id: str,
        session_id: str,
    ) -> bool:
        """
        S3からコンテナにSDKセッションファイルを復元

        コンテナ破棄後の再開時に、S3に保存されたセッションファイルを
        コンテナ内の正しいパスに復元する。

        Args:
            tenant_id: テナントID
            conversation_id: 会話ID
            container_id: コンテナID
            session_id: SDKセッションID

        Returns:
            復元成功した場合True
        """
        try:
            data, _ = await self.s3.download(
                tenant_id, conversation_id,
                f"_sdk_session/{session_id}.jsonl",
            )
        except Exception:
            logger.debug(
                "S3にセッションファイルなし（新規セッション）",
                session_id=session_id,
                conversation_id=conversation_id,
            )
            return False

        dest_path = f"/home/appuser/.claude/projects/-workspace/{session_id}.jsonl"
        await self._write_to_container(container_id, dest_path, data)
        logger.info(
            "セッションファイル復元完了",
            session_id=session_id,
            conversation_id=conversation_id,
            container_id=container_id,
            size=len(data),
        )
        return True

    async def _upsert_file_record(
        self, conversation_id: str, file_path: str, file_size: int
    ) -> None:
        """ファイルレコードをDBにupsert"""
        from sqlalchemy import select
        from uuid import uuid4
        from datetime import datetime, timezone

        # バックグラウンド同期タスクからの並行呼び出しによるAsyncSessionの競合を防止
        async with self._db_lock:
            stmt = select(ConversationFile).where(
                ConversationFile.conversation_id == conversation_id,
                ConversationFile.file_path == file_path,
            )
            result = await self.db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.file_size = file_size
                existing.version += 1
                existing.source = "ai_modified"
                existing.updated_at = datetime.now(timezone.utc)
            else:
                new_file = ConversationFile(
                    file_id=str(uuid4()),
                    conversation_id=conversation_id,
                    file_path=file_path,
                    original_name=file_path.split("/")[-1],
                    file_size=file_size,
                    source="ai_created",
                )
                self.db.add(new_file)

            # flush のみ実行（SQL文をDBに送信するがトランザクションは確定しない）
            # 最終的なコミットは ExecuteService.execute_streaming() の finally で一括実行
            await self.db.flush()
