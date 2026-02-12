"""
ワークスペースファイル同期
S3 ↔ コンテナ間のファイル同期を担当

同期フロー:
  - sync_to_container: S3 → コンテナ（コンテナ割り当て時）
  - sync_from_container: コンテナ → S3（実行完了時、コンテナ破棄時）
"""
import asyncio
import io
import tarfile

import aiodocker
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

        synced = 0
        for file_record in files:
            # 防御的チェック: 予約パスがDBに存在していた場合はスキップ
            if self._is_reserved_path(file_record.file_path):
                logger.warning(
                    "予約パスのファイルレコード検出（スキップ）",
                    file_path=file_record.file_path,
                    conversation_id=conversation_id,
                )
                continue
            try:
                data, _ = await self.s3.download(
                    tenant_id, conversation_id, file_record.file_path
                )
                await self._write_to_container(
                    container_id,
                    f"/workspace/{file_record.file_path}",
                    data,
                )
                synced += 1
            except Exception as e:
                logger.error(
                    "ファイル同期エラー（S3→コンテナ）",
                    file_path=file_record.file_path,
                    container_id=container_id,
                    error=str(e),
                )

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

        if not file_paths:
            return 0

        synced = 0
        for file_path in file_paths:
            try:
                data = await self._read_from_container(
                    container_id, f"/workspace/{file_path}"
                )
                if data is not None:
                    await self.s3.upload(
                        tenant_id, conversation_id, file_path, data
                    )
                    await self._upsert_file_record(conversation_id, file_path, len(data))
                    synced += 1
            except Exception as e:
                logger.error(
                    "ファイル同期エラー（コンテナ→S3）",
                    file_path=file_path,
                    container_id=container_id,
                    error=str(e),
                )

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
        """docker cpでコンテナにファイルを書き込む"""
        # tarアーカイブを作成
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            file_info = tarfile.TarInfo(name=dest_path.split("/")[-1])
            file_info.size = len(data)
            file_info.uid = 1000
            file_info.gid = 1000
            tar.addfile(file_info, io.BytesIO(data))
        tar_buffer.seek(0)

        # 親ディレクトリを確保
        parent_dir = "/".join(dest_path.split("/")[:-1])
        await self.lifecycle.exec_in_container(
            container_id, ["mkdir", "-p", parent_dir]
        )

        # docker putでコンテナに送信
        container = await self.lifecycle.docker.containers.get(container_id)
        await container.put_archive(parent_dir, tar_buffer.read())

    async def _read_from_container(
        self, container_id: str, src_path: str
    ) -> bytes | None:
        """docker cpでコンテナからファイルを読み出す"""
        try:
            container = await self.lifecycle.docker.containers.get(container_id)
            tar_stream = await container.get_archive(src_path)

            tar_buffer = io.BytesIO()
            # tar_stream is a dict with 'body' containing the tar data
            if isinstance(tar_stream, dict):
                body = tar_stream.get("body", b"")
                if hasattr(body, "read"):
                    tar_buffer.write(await body.read())
                else:
                    tar_buffer.write(body)
            else:
                async for chunk in tar_stream:
                    tar_buffer.write(chunk)

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        extracted = tar.extractfile(member)
                        if extracted:
                            return extracted.read()
            return None
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
