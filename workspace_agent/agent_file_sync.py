"""
エージェントコンテナ内S3同期モジュール

AgentCoreのmicroVM内で動作し、ホスト側の docker exec ベースの同期を代替する。
boto3を使用し、AgentCoreの実行ロール(Execution Role)でS3にアクセスする。

機能:
  - restore_from_s3(): S3 → /workspace にファイルをダウンロード（セッション開始時）
  - sync_to_s3(): /workspace → S3 に全ファイルをアップロード（Turn完了後）
  - restore_session_file(): S3の _sdk_session/{id}.jsonl → SDK projects dir に復元
  - save_session_file(): SDK セッションファイルをS3に保存
  - get_file_manifest(): ワークスペース内の全ファイル一覧を返却
"""

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 同期対象から除外するディレクトリ名
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

# 同期対象から除外する拡張子・ファイル名
_EXCLUDED_EXTENSIONS = frozenset({
    ".pyc",
    ".pyo",
    ".DS_Store",
})

# 予約プレフィックス（専用の save/restore で管理）
_RESERVED_PREFIXES = frozenset({
    "_sdk_session/",
})

# SDK セッションファイルのベースパス
_SDK_PROJECTS_DIR = "/home/appuser/.claude/projects/-workspace"

# ワークスペースルート
_WORKSPACE_ROOT = "/workspace"


def _should_exclude(file_path: str) -> bool:
    """同期対象外のファイルかチェック"""
    segments = file_path.split("/")
    for seg in segments[:-1]:
        if seg in _EXCLUDED_DIR_NAMES:
            return True
    filename = segments[-1] if segments else ""
    for ext in _EXCLUDED_EXTENSIONS:
        if filename.endswith(ext) or filename == ext.lstrip("."):
            return True
    return False


def _is_reserved_path(file_path: str) -> bool:
    """予約プレフィックスに該当するパスかチェック"""
    return any(
        file_path.startswith(prefix) or file_path == prefix.rstrip("/")
        for prefix in _RESERVED_PREFIXES
    )


def _get_s3_client(region: str | None = None):
    """boto3 S3クライアントを取得"""
    import boto3
    return boto3.client(
        "s3",
        region_name=region or os.environ.get("AWS_REGION", "us-west-2"),
    )


async def restore_from_s3(
    s3_bucket: str,
    s3_prefix: str,
    tenant_id: str,
    conversation_id: str,
    region: str | None = None,
) -> int:
    """
    S3 → /workspace にファイルをダウンロード（セッション開始時）

    Returns:
        復元したファイル数
    """
    def _do_restore() -> int:
        s3 = _get_s3_client(region)
        prefix = f"{s3_prefix}{tenant_id}/{conversation_id}/"
        restored = 0

        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                s3_key = obj["Key"]
                relative = s3_key[len(prefix):]

                # 予約パスと除外パターンをスキップ
                if _is_reserved_path(relative) or _should_exclude(relative):
                    continue

                local_path = Path(_WORKSPACE_ROOT) / relative
                local_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    s3.download_file(s3_bucket, s3_key, str(local_path))
                    restored += 1
                except Exception as e:
                    logger.error(
                        "S3ダウンロードエラー: %s -> %s: %s",
                        s3_key, local_path, e,
                    )

        return restored

    restored = await asyncio.to_thread(_do_restore)
    logger.info(
        "S3→ワークスペース復元完了: %d files",
        restored,
    )
    return restored


async def sync_to_s3(
    s3_bucket: str,
    s3_prefix: str,
    tenant_id: str,
    conversation_id: str,
    region: str | None = None,
) -> int:
    """
    /workspace → S3 に全ファイルをアップロード（Turn完了後）

    Returns:
        同期したファイル数
    """
    def _do_sync() -> int:
        s3 = _get_s3_client(region)
        prefix = f"{s3_prefix}{tenant_id}/{conversation_id}/"
        synced = 0

        workspace = Path(_WORKSPACE_ROOT)
        if not workspace.exists():
            return 0

        for file_path in workspace.rglob("*"):
            if not file_path.is_file():
                continue

            relative = str(file_path.relative_to(workspace))

            if _is_reserved_path(relative) or _should_exclude(relative):
                continue

            s3_key = f"{prefix}{relative}"
            try:
                s3.upload_file(str(file_path), s3_bucket, s3_key)
                synced += 1
            except Exception as e:
                logger.error(
                    "S3アップロードエラー: %s -> %s: %s",
                    file_path, s3_key, e,
                )

        return synced

    synced = await asyncio.to_thread(_do_sync)
    logger.info(
        "ワークスペース→S3同期完了: %d files",
        synced,
    )
    return synced


async def restore_session_file(
    s3_bucket: str,
    s3_prefix: str,
    tenant_id: str,
    conversation_id: str,
    session_id: str,
    region: str | None = None,
) -> bool:
    """
    S3の _sdk_session/{session_id}.jsonl → SDK projects dir に復元

    Returns:
        復元成功した場合True
    """
    def _do_restore() -> bool:
        s3 = _get_s3_client(region)
        s3_key = f"{s3_prefix}{tenant_id}/{conversation_id}/_sdk_session/{session_id}.jsonl"
        dest_path = Path(_SDK_PROJECTS_DIR) / f"{session_id}.jsonl"

        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(s3_bucket, s3_key, str(dest_path))
            logger.info(
                "セッションファイル復元完了: %s (%d bytes)",
                session_id, dest_path.stat().st_size,
            )
            return True
        except s3.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                logger.debug(
                    "S3にセッションファイルなし（新規セッション）: %s",
                    session_id,
                )
            else:
                logger.error("セッションファイル復元エラー: %s", e)
            return False
        except Exception as e:
            logger.error("セッションファイル復元エラー: %s", e)
            return False

    return await asyncio.to_thread(_do_restore)


async def save_session_file(
    s3_bucket: str,
    s3_prefix: str,
    tenant_id: str,
    conversation_id: str,
    session_id: str,
    region: str | None = None,
) -> bool:
    """
    SDK セッションファイルをS3に保存

    Returns:
        保存成功した場合True
    """
    def _do_save() -> bool:
        s3 = _get_s3_client(region)
        session_path = Path(_SDK_PROJECTS_DIR) / f"{session_id}.jsonl"

        if not session_path.exists():
            logger.debug(
                "セッションファイル未検出（スキップ）: %s",
                session_id,
            )
            return False

        s3_key = f"{s3_prefix}{tenant_id}/{conversation_id}/_sdk_session/{session_id}.jsonl"
        try:
            s3.upload_file(str(session_path), s3_bucket, s3_key)
            logger.info(
                "セッションファイルS3保存完了: %s (%d bytes)",
                session_id, session_path.stat().st_size,
            )
            return True
        except Exception as e:
            logger.error("セッションファイル保存エラー: %s", e)
            return False

    return await asyncio.to_thread(_do_save)


def get_file_manifest() -> list[dict]:
    """
    ワークスペース内の全ファイル一覧（パス+サイズ）を返却

    Returns:
        [{"path": "relative/path", "size": 1234}, ...]
    """
    workspace = Path(_WORKSPACE_ROOT)
    if not workspace.exists():
        return []

    manifest = []
    for file_path in workspace.rglob("*"):
        if not file_path.is_file():
            continue

        relative = str(file_path.relative_to(workspace))

        if _is_reserved_path(relative) or _should_exclude(relative):
            continue

        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0

        manifest.append({"path": relative, "size": size})

    return manifest
