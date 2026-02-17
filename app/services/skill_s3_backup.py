"""
Skills S3バックアップサービス

スキルファイルのS3 write-throughバックアップと災害復旧を担当。
通常の読み取りはローカルファイルシステムから行い、
書き込み時にS3へも同期することで耐障害性を確保する。
"""
import asyncio
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import structlog

from app.config import get_settings
from app.infrastructure.metrics import get_s3_operations

logger = structlog.get_logger(__name__)


class SkillS3Backup:
    """
    Skills S3バックアップ

    write-through方式でスキルファイルをS3にミラーリングする。
    ローカルファイルシステムが常に正（authoritative）であり、
    S3はバックアップ・災害復旧用。
    """

    def __init__(self):
        _settings = get_settings()

        config = Config(
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
            connect_timeout=10,
            read_timeout=30,
        )

        self.client = boto3.client(
            "s3",
            region_name=_settings.aws_region,
            aws_access_key_id=_settings.aws_access_key_id,
            aws_secret_access_key=_settings.aws_secret_access_key,
            config=config,
        )
        self.bucket = _settings.s3_bucket_name
        self.prefix = _settings.s3_skills_prefix.rstrip("/")
        self._metrics = get_s3_operations()

    def _get_s3_key(self, tenant_id: str, skill_name: str, filename: str) -> str:
        """S3キーを生成"""
        return f"{self.prefix}/tenant_{tenant_id}/.claude/skills/{skill_name}/{filename}"

    def _get_skill_prefix(self, tenant_id: str, skill_name: str) -> str:
        """スキルのS3プレフィックスを取得"""
        return f"{self.prefix}/tenant_{tenant_id}/.claude/skills/{skill_name}/"

    def _get_tenant_prefix(self, tenant_id: str) -> str:
        """テナントのS3プレフィックスを取得"""
        return f"{self.prefix}/tenant_{tenant_id}/.claude/skills/"

    async def upload_skill_files(
        self,
        tenant_id: str,
        skill_name: str,
        files: dict[str, str],
    ) -> int:
        """
        スキルファイル群をS3にアップロード

        Args:
            tenant_id: テナントID
            skill_name: スキル名
            files: {"filename": "content", ...}

        Returns:
            アップロードしたファイル数
        """
        uploaded = 0
        for filename, content in files.items():
            key = self._get_s3_key(tenant_id, skill_name, filename)
            try:
                await asyncio.to_thread(
                    self.client.put_object,
                    Bucket=self.bucket,
                    Key=key,
                    Body=content.encode("utf-8"),
                    ContentType="text/plain; charset=utf-8",
                )
                uploaded += 1
                self._metrics.inc(operation="skill_upload", status="success")
            except Exception as e:
                logger.error(
                    "S3スキルアップロードエラー",
                    key=key,
                    error=str(e),
                )
                self._metrics.inc(operation="skill_upload", status="error")
                raise

        logger.info(
            "S3スキルアップロード完了",
            tenant_id=tenant_id,
            skill_name=skill_name,
            count=uploaded,
        )
        return uploaded

    async def delete_skill_files(
        self,
        tenant_id: str,
        skill_name: str,
    ) -> int:
        """
        スキルのS3ファイルを全削除

        Args:
            tenant_id: テナントID
            skill_name: スキル名

        Returns:
            削除したファイル数
        """
        prefix = self._get_skill_prefix(tenant_id, skill_name)

        try:
            # オブジェクト一覧を取得
            paginator = self.client.get_paginator("list_objects_v2")

            def _list_keys():
                keys = []
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        keys.append(obj["Key"])
                return keys

            keys = await asyncio.to_thread(_list_keys)
        except Exception as e:
            logger.error(
                "S3スキル一覧取得エラー",
                prefix=prefix,
                error=str(e),
            )
            self._metrics.inc(operation="skill_delete", status="error")
            raise

        # 各オブジェクトを削除
        deleted = 0
        for key in keys:
            try:
                await asyncio.to_thread(
                    self.client.delete_object,
                    Bucket=self.bucket,
                    Key=key,
                )
                deleted += 1
            except Exception as e:
                logger.error("S3スキルファイル削除エラー", key=key, error=str(e))

        self._metrics.inc(operation="skill_delete", status="success")
        logger.info(
            "S3スキル削除完了",
            tenant_id=tenant_id,
            skill_name=skill_name,
            count=deleted,
        )
        return deleted

    async def restore_tenant_skills(
        self,
        tenant_id: str,
        base_path: Path,
    ) -> int:
        """
        テナントの全スキルをS3からローカルに復元

        Args:
            tenant_id: テナントID
            base_path: スキルのベースパス（例: /skills）

        Returns:
            復元したファイル数
        """
        tenant_prefix = self._get_tenant_prefix(tenant_id)

        try:
            paginator = self.client.get_paginator("list_objects_v2")

            def _list_objects():
                objects = []
                for page in paginator.paginate(
                    Bucket=self.bucket, Prefix=tenant_prefix
                ):
                    for obj in page.get("Contents", []):
                        objects.append(obj["Key"])
                return objects

            keys = await asyncio.to_thread(_list_objects)
        except Exception as e:
            logger.error(
                "S3スキル復元一覧取得エラー",
                tenant_prefix=tenant_prefix,
                error=str(e),
            )
            self._metrics.inc(operation="skill_restore", status="error")
            raise

        if not keys:
            logger.info(
                "S3にスキルデータなし（復元スキップ）",
                tenant_id=tenant_id,
            )
            return 0

        restored = 0
        for key in keys:
            try:
                # S3キーからローカルパスを算出
                # key: skills/tenant_{id}/.claude/skills/{name}/{file}
                # local: /skills/tenant_{id}/.claude/skills/{name}/{file}
                relative = key[len(self.prefix) :].lstrip("/")
                local_path = base_path / relative

                # ダウンロード
                response = await asyncio.to_thread(
                    self.client.get_object,
                    Bucket=self.bucket,
                    Key=key,
                )
                content = await asyncio.to_thread(response["Body"].read)

                # ローカルに書き込み
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(content)
                restored += 1

            except Exception as e:
                logger.error(
                    "S3スキルファイル復元エラー",
                    key=key,
                    error=str(e),
                )

        self._metrics.inc(operation="skill_restore", status="success")
        logger.info(
            "S3スキル復元完了",
            tenant_id=tenant_id,
            restored_files=restored,
            total_keys=len(keys),
        )
        return restored

    async def has_tenant_skills(self, tenant_id: str) -> bool:
        """
        S3にテナントのスキルが存在するかチェック

        Args:
            tenant_id: テナントID

        Returns:
            スキルが存在する場合True
        """
        tenant_prefix = self._get_tenant_prefix(tenant_id)

        try:
            response = await asyncio.to_thread(
                self.client.list_objects_v2,
                Bucket=self.bucket,
                Prefix=tenant_prefix,
                MaxKeys=1,
            )
            return response.get("KeyCount", 0) > 0
        except Exception as e:
            logger.error(
                "S3スキル存在チェックエラー",
                tenant_prefix=tenant_prefix,
                error=str(e),
            )
            return False
