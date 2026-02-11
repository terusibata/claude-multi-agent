"""
S3ストレージバックエンド

すべてのS3アクセスはこのクラスを経由する。
ワークスペースファイルの保存・取得・同期を担当。
asyncio.to_thread()による非同期I/Oでイベントループをブロックしない。
"""
import asyncio
import io
import mimetypes
import os
from collections.abc import AsyncIterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import structlog

from app.config import get_settings
from app.infrastructure.metrics import get_s3_operations

logger = structlog.get_logger(__name__)


class S3StorageBackend:
    """
    S3ストレージ操作

    すべてのS3アクセスはこのクラスを経由する
    asyncio.to_thread()で同期boto3呼び出しをスレッドプールにオフロード
    """

    def __init__(self):
        _settings = get_settings()

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
            region_name=_settings.aws_region,
            aws_access_key_id=_settings.aws_access_key_id,
            aws_secret_access_key=_settings.aws_secret_access_key,
            config=config,
        )
        self.bucket = _settings.s3_bucket_name
        self.prefix = _settings.s3_workspace_prefix.rstrip('/')
        self.chunk_size = _settings.s3_chunk_size
        self._metrics = get_s3_operations()

    def get_key(self, tenant_id: str, session_id: str, file_path: str) -> str:
        """S3キーを生成"""
        return f"{self.prefix}/{tenant_id}/{session_id}/{file_path}"

    def get_prefix(self, tenant_id: str, session_id: str) -> str:
        """セッションのプレフィックスを取得"""
        return f"{self.prefix}/{tenant_id}/{session_id}/"

    async def upload(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """ファイルをS3にアップロード"""
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            await asyncio.to_thread(
                self.client.put_object,
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
        content_length: int | None = None,
    ) -> str:
        """ストリームからS3にアップロード（大きなファイル用）"""
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            extra_args = {'ContentType': content_type}

            await asyncio.to_thread(
                self.client.upload_fileobj,
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
        """ファイルをS3からダウンロード"""
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            response = await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.bucket,
                Key=key,
            )
            content = await asyncio.to_thread(response['Body'].read)
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
        """ファイルをS3からストリーミングダウンロード（メモリ効率化）"""
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            response = await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.bucket,
                Key=key,
            )
            body = response['Body']

            try:
                while True:
                    chunk = await asyncio.to_thread(body.read, self.chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                # 正常終了・異常終了問わずBodyをクローズ
                await asyncio.to_thread(body.close)

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
        """ファイルをS3からローカルに直接ダウンロード（メモリ使用最小化）"""
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            await asyncio.to_thread(
                self.client.download_file,
                self.bucket,
                key,
                local_path,
            )

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
        """ファイルのメタデータを取得（head_objectでダウンロードなし）"""
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            response = await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=key,
            )
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
        """ファイル一覧を取得"""
        prefix = self.get_prefix(tenant_id, session_id)

        try:
            paginator = self.client.get_paginator('list_objects_v2')

            def _list_all():
                result = []
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    for obj in page.get('Contents', []):
                        relative_path = obj['Key'][len(prefix):]
                        if relative_path:
                            result.append({
                                'file_path': relative_path,
                                'file_size': obj['Size'],
                                'last_modified': obj['LastModified'],
                                'storage_class': obj.get('StorageClass', 'STANDARD'),
                            })
                return result

            files = await asyncio.to_thread(_list_all)

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
        """ファイルを削除"""
        key = self.get_key(tenant_id, session_id, file_path)

        try:
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.bucket,
                Key=key,
            )
            self._metrics.inc(operation="delete", status="success")
            return True
        except Exception as e:
            logger.error("S3削除エラー", key=key, error=str(e))
            self._metrics.inc(operation="delete", status="error")
            raise

    async def delete_prefix(
        self,
        tenant_id: str,
        session_id: str,
    ) -> int:
        """
        セッション配下の全ファイルを削除

        Args:
            tenant_id: テナントID
            session_id: セッションID

        Returns:
            削除されたファイル数
        """
        files = await self.list_files(tenant_id, session_id)
        deleted = 0

        for file_info in files:
            try:
                await self.delete(tenant_id, session_id, file_info['file_path'])
                deleted += 1
            except Exception as e:
                logger.error(
                    "S3ファイル削除エラー",
                    file_path=file_info['file_path'],
                    error=str(e),
                )

        logger.info("S3プレフィックス削除完了", count=deleted)
        return deleted

    async def exists(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> bool:
        """ファイルの存在確認"""
        key = self.get_key(tenant_id, session_id, file_path)
        try:
            await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=key,
            )
            return True
        except ClientError:
            return False

    async def sync_to_local(
        self,
        tenant_id: str,
        session_id: str,
        local_dir: str,
    ) -> list[str]:
        """S3からローカルにファイルを同期（エージェント実行用）"""
        files = await self.list_files(tenant_id, session_id)
        synced = []

        for file_info in files:
            file_path = file_info['file_path']
            local_path = os.path.join(local_dir, file_path)

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
        """ローカルからS3にファイルを同期（エージェント実行後）"""
        synced = []

        def _walk_dir():
            result = []
            for root, dirs, files in os.walk(local_dir):
                for filename in files:
                    local_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(local_path, local_dir)
                    content_type, _ = mimetypes.guess_type(filename)
                    content_type = content_type or 'application/octet-stream'
                    result.append((local_path, relative_path, content_type))
            return result

        file_list = await asyncio.to_thread(_walk_dir)

        for local_path, relative_path, content_type in file_list:
            with open(local_path, 'rb') as f:
                await self.upload_stream(
                    tenant_id, session_id, relative_path,
                    f, content_type
                )
            synced.append(relative_path)

        logger.info("ローカル→S3同期完了", count=len(synced))
        return synced
