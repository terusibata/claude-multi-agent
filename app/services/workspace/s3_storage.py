"""
S3ストレージバックエンド

すべてのS3アクセスはこのクラスを経由する。
ワークスペースファイルの保存・取得・同期を担当。
"""
import mimetypes
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class S3StorageBackend:
    """
    S3ストレージ操作

    すべてのS3アクセスはこのクラスを経由する
    """

    def __init__(self):
        self.client = boto3.client(
            's3',
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self.bucket = settings.s3_bucket_name
        self.prefix = settings.s3_workspace_prefix.rstrip('/')

    def get_key(self, tenant_id: str, session_id: str, file_path: str) -> str:
        """
        S3キーを生成

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス

        Returns:
            S3キー (例: workspaces/tenant-001/session-001/uploads/file.csv)
        """
        return f"{self.prefix}/{tenant_id}/{session_id}/{file_path}"

    def get_prefix(self, tenant_id: str, session_id: str) -> str:
        """
        セッションのプレフィックスを取得

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            S3プレフィックス
        """
        return f"{self.prefix}/{tenant_id}/{session_id}/"

    async def upload(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        ファイルをS3にアップロード

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス
            content: ファイル内容
            content_type: MIMEタイプ

        Returns:
            S3キー
        """
        key = self.get_key(tenant_id, session_id, file_path)

        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )

        logger.info("S3アップロード完了", key=key, size=len(content))
        return key

    async def download(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> tuple[bytes, str]:
        """
        ファイルをS3からダウンロード

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス

        Returns:
            (content, content_type)

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            content = response['Body'].read()
            content_type = response.get('ContentType', 'application/octet-stream')
            return content, content_type
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise FileNotFoundError(f"File not found: {file_path}")
            raise

    async def list_files(
        self,
        tenant_id: str,
        session_id: str,
    ) -> list[dict]:
        """
        ファイル一覧を取得

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            ファイル情報のリスト
        """
        prefix = self.get_prefix(tenant_id, session_id)

        files = []
        paginator = self.client.get_paginator('list_objects_v2')

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                # プレフィックスを除いた相対パス
                relative_path = obj['Key'][len(prefix):]
                if relative_path:  # 空でない場合のみ
                    files.append({
                        'file_path': relative_path,
                        'file_size': obj['Size'],
                        'last_modified': obj['LastModified'],
                        'storage_class': obj.get('StorageClass', 'STANDARD'),
                    })

        return files

    async def delete(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> bool:
        """
        ファイルを削除

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス

        Returns:
            成功フラグ
        """
        key = self.get_key(tenant_id, session_id, file_path)
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    async def exists(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> bool:
        """
        ファイルの存在確認

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス

        Returns:
            存在するかどうか
        """
        key = self.get_key(tenant_id, session_id, file_path)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    async def sync_to_local(
        self,
        tenant_id: str,
        session_id: str,
        local_dir: str,
    ) -> list[str]:
        """
        S3からローカルにファイルを同期（エージェント実行用）

        Args:
            tenant_id: テナントID
            session_id: セッションID
            local_dir: ローカルディレクトリパス

        Returns:
            同期されたファイルパスのリスト
        """
        files = await self.list_files(tenant_id, session_id)
        synced = []

        for file_info in files:
            file_path = file_info['file_path']
            local_path = os.path.join(local_dir, file_path)

            # ディレクトリ作成
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            # ダウンロード
            content, _ = await self.download(tenant_id, session_id, file_path)
            with open(local_path, 'wb') as f:
                f.write(content)

            synced.append(file_path)

        logger.info("S3→ローカル同期完了", count=len(synced))
        return synced

    async def sync_from_local(
        self,
        tenant_id: str,
        session_id: str,
        local_dir: str,
    ) -> list[str]:
        """
        ローカルからS3にファイルを同期（エージェント実行後）

        Args:
            tenant_id: テナントID
            session_id: セッションID
            local_dir: ローカルディレクトリパス

        Returns:
            同期されたファイルパスのリスト
        """
        synced = []

        for root, dirs, files in os.walk(local_dir):
            for filename in files:
                local_path = os.path.join(root, filename)
                relative_path = os.path.relpath(local_path, local_dir)

                # MIMEタイプ推測
                content_type, _ = mimetypes.guess_type(filename)
                content_type = content_type or 'application/octet-stream'

                # アップロード
                with open(local_path, 'rb') as f:
                    content = f.read()

                await self.upload(
                    tenant_id, session_id, relative_path,
                    content, content_type
                )
                synced.append(relative_path)

        logger.info("ローカル→S3同期完了", count=len(synced))
        return synced
