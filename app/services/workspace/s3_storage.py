"""
S3ストレージバックエンド

すべてのS3アクセスはこのクラスを経由する。
ワークスペースファイルの保存・取得・同期を担当。
メモリ効率を考慮したストリーミング処理を実装。
"""
import io
import mimetypes
import os
from typing import AsyncIterator, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import structlog

from app.config import get_settings
from app.infrastructure.metrics import get_s3_operations

logger = structlog.get_logger(__name__)
settings = get_settings()


class S3StorageBackend:
    """
    S3ストレージ操作

    すべてのS3アクセスはこのクラスを経由する
    メモリ効率を考慮したチャンク処理を実装
    """

    def __init__(self):
        # boto3 の設定（リトライ設定を含む）
        config = Config(
            retries={
                'max_attempts': 3,
                'mode': 'standard'
            },
            connect_timeout=10,
            read_timeout=30,
        )

        self.client = boto3.client(
            's3',
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=config,
        )
        self.bucket = settings.s3_bucket_name
        self.prefix = settings.s3_workspace_prefix.rstrip('/')
        self.chunk_size = settings.s3_chunk_size
        self._metrics = get_s3_operations()

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

        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )

            logger.info("S3アップロード完了", key=key, size=len(content))
            self._metrics.inc(operation="upload", status="success")
            return key

        except Exception as e:
            logger.error("S3アップロードエラー", key=key, error=str(e))
            self._metrics.inc(operation="upload", status="error")
            raise

    async def upload_stream(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        stream: io.IOBase,
        content_type: str = "application/octet-stream",
        content_length: Optional[int] = None,
    ) -> str:
        """
        ストリームからS3にアップロード（大きなファイル用）

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス
            stream: 読み取りストリーム
            content_type: MIMEタイプ
            content_length: コンテンツ長（既知の場合）

        Returns:
            S3キー
        """
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            extra_args = {'ContentType': content_type}

            # マルチパートアップロードを使用
            self.client.upload_fileobj(
                stream,
                self.bucket,
                key,
                ExtraArgs=extra_args,
            )

            logger.info("S3ストリームアップロード完了", key=key)
            self._metrics.inc(operation="upload_stream", status="success")
            return key

        except Exception as e:
            logger.error("S3ストリームアップロードエラー", key=key, error=str(e))
            self._metrics.inc(operation="upload_stream", status="error")
            raise

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

            self._metrics.inc(operation="download", status="success")
            return content, content_type

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                self._metrics.inc(operation="download", status="not_found")
                raise FileNotFoundError(f"File not found: {file_path}")
            self._metrics.inc(operation="download", status="error")
            raise

    async def download_stream(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> AsyncIterator[bytes]:
        """
        ファイルをS3からストリーミングダウンロード（メモリ効率化）

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス

        Yields:
            バイトチャンク

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response['Body']

            # チャンク単位で読み込み（メモリ効率化）
            while True:
                chunk = body.read(self.chunk_size)
                if not chunk:
                    break
                yield chunk

            body.close()
            self._metrics.inc(operation="download_stream", status="success")

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                self._metrics.inc(operation="download_stream", status="not_found")
                raise FileNotFoundError(f"File not found: {file_path}")
            self._metrics.inc(operation="download_stream", status="error")
            raise

    async def download_to_file(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        local_path: str,
    ) -> str:
        """
        ファイルをS3からローカルに直接ダウンロード（メモリ使用最小化）

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス
            local_path: ローカル保存先パス

        Returns:
            ローカルファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            # ディレクトリ作成
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            # ファイルに直接ダウンロード（メモリ効率的）
            self.client.download_file(self.bucket, key, local_path)

            logger.debug("S3ファイルダウンロード完了", key=key, local_path=local_path)
            self._metrics.inc(operation="download_to_file", status="success")
            return local_path

        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                self._metrics.inc(operation="download_to_file", status="not_found")
                raise FileNotFoundError(f"File not found: {file_path}")
            self._metrics.inc(operation="download_to_file", status="error")
            raise

    async def get_metadata(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> dict:
        """
        ファイルのメタデータを取得

        Args:
            tenant_id: テナントID
            session_id: セッションID
            file_path: ファイルパス

        Returns:
            メタデータ辞書
        """
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return {
                'content_type': response.get('ContentType', 'application/octet-stream'),
                'content_length': response.get('ContentLength', 0),
                'last_modified': response.get('LastModified'),
                'etag': response.get('ETag'),
            }
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
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

        try:
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

            self._metrics.inc(operation="list", status="success")
            return files

        except Exception as e:
            logger.error("S3ファイル一覧取得エラー", prefix=prefix, error=str(e))
            self._metrics.inc(operation="list", status="error")
            raise

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

        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            self._metrics.inc(operation="delete", status="success")
            return True
        except Exception as e:
            logger.error("S3削除エラー", key=key, error=str(e))
            self._metrics.inc(operation="delete", status="error")
            raise

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
        メモリ効率的にファイル単位でダウンロード

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

            # ファイルに直接ダウンロード（メモリ効率的）
            await self.download_to_file(
                tenant_id, session_id, file_path, local_path
            )
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
        メモリ効率的にファイル単位でアップロード

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

                # ファイルから直接ストリームアップロード
                with open(local_path, 'rb') as f:
                    await self.upload_stream(
                        tenant_id, session_id, relative_path,
                        f, content_type
                    )

                synced.append(relative_path)

        logger.info("ローカル→S3同期完了", count=len(synced))
        return synced
