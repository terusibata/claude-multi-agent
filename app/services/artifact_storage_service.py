"""
アーティファクトストレージサービス
ローカルファイルシステムまたはS3へのファイル保存を抽象化
"""
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import aiofiles

from app.config import get_settings


class ArtifactStorage(ABC):
    """アーティファクトストレージの抽象基底クラス"""

    @abstractmethod
    async def save(
        self,
        content: str,
        tenant_id: str,
        session_id: str,
        filename: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        ファイルを保存

        Args:
            content: ファイル内容
            tenant_id: テナントID
            session_id: セッションID
            filename: ファイル名

        Returns:
            (file_path, s3_key) のタプル
            ローカルの場合: (file_path, None)
            S3の場合: (None, s3_key)
        """
        pass

    @abstractmethod
    async def read(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> str:
        """
        ファイルを読み込み

        Args:
            file_path: ローカルファイルパス
            s3_key: S3キー

        Returns:
            ファイル内容
        """
        pass

    @abstractmethod
    async def delete(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> bool:
        """
        ファイルを削除

        Args:
            file_path: ローカルファイルパス
            s3_key: S3キー

        Returns:
            削除成功: True, 失敗: False
        """
        pass


class LocalArtifactStorage(ArtifactStorage):
    """ローカルファイルシステムを使用したストレージ"""

    def __init__(self, base_path: str = "/artifacts"):
        self.base_path = Path(base_path)
        # ベースディレクトリを作成
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, tenant_id: str, session_id: str, filename: str) -> Path:
        """ファイルパスを生成"""
        # /artifacts/tenant_{tenant_id}/{session_id}/{filename}
        file_dir = self.base_path / f"tenant_{tenant_id}" / session_id
        file_dir.mkdir(parents=True, exist_ok=True)
        return file_dir / filename

    async def save(
        self,
        content: str,
        tenant_id: str,
        session_id: str,
        filename: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """ファイルを保存"""
        file_path = self._get_file_path(tenant_id, session_id, filename)

        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write(content)

        return (str(file_path), None)

    async def read(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> str:
        """ファイルを読み込み"""
        if not file_path:
            raise ValueError("file_path is required for local storage")

        async with aiofiles.open(file_path, mode="r", encoding="utf-8") as f:
            return await f.read()

    async def delete(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> bool:
        """ファイルを削除"""
        if not file_path:
            return False

        try:
            Path(file_path).unlink(missing_ok=True)
            return True
        except Exception:
            return False


class S3ArtifactStorage(ArtifactStorage):
    """AWS S3を使用したストレージ（本番環境用）"""

    def __init__(
        self,
        bucket_name: str,
        prefix: str = "artifacts/",
        region: str = "us-west-2",
    ):
        self.bucket_name = bucket_name
        self.prefix = prefix
        self.region = region

        # boto3は遅延インポート（開発環境では不要なため）
        try:
            import boto3
            self.s3_client = boto3.client("s3", region_name=region)
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 storage. Install with: pip install boto3"
            )

    def _get_s3_key(self, tenant_id: str, session_id: str, filename: str) -> str:
        """S3キーを生成"""
        # artifacts/tenant_{tenant_id}/{session_id}/{filename}
        return f"{self.prefix}tenant_{tenant_id}/{session_id}/{filename}"

    async def save(
        self,
        content: str,
        tenant_id: str,
        session_id: str,
        filename: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """ファイルをS3に保存"""
        s3_key = self._get_s3_key(tenant_id, session_id, filename)

        # MIMEタイプを推定
        mime_type, _ = mimetypes.guess_type(filename)
        extra_args = {}
        if mime_type:
            extra_args["ContentType"] = mime_type

        # S3にアップロード
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=s3_key,
            Body=content.encode("utf-8"),
            **extra_args,
        )

        return (None, s3_key)

    async def read(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> str:
        """ファイルをS3から読み込み"""
        if not s3_key:
            raise ValueError("s3_key is required for S3 storage")

        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
        return response["Body"].read().decode("utf-8")

    async def delete(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> bool:
        """ファイルをS3から削除"""
        if not s3_key:
            return False

        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except Exception:
            return False


class ArtifactStorageService:
    """
    アーティファクトストレージサービス
    環境に応じてローカルストレージまたはS3を使用
    """

    def __init__(self):
        settings = get_settings()

        if settings.artifacts_storage_type == "s3":
            if not settings.artifacts_s3_bucket:
                raise ValueError(
                    "artifacts_s3_bucket must be set when using S3 storage"
                )
            self.storage = S3ArtifactStorage(
                bucket_name=settings.artifacts_s3_bucket,
                prefix=settings.artifacts_s3_prefix,
                region=settings.aws_region,
            )
        else:
            # デフォルトはローカルストレージ
            self.storage = LocalArtifactStorage(
                base_path=settings.artifacts_base_path
            )

    async def save_artifact(
        self,
        content: str,
        tenant_id: str,
        session_id: str,
        filename: str,
    ) -> tuple[Optional[str], Optional[str], int]:
        """
        アーティファクトを保存

        Args:
            content: ファイル内容
            tenant_id: テナントID
            session_id: セッションID
            filename: ファイル名

        Returns:
            (file_path, s3_key, file_size) のタプル
        """
        file_path, s3_key = await self.storage.save(
            content=content,
            tenant_id=tenant_id,
            session_id=session_id,
            filename=filename,
        )

        # ファイルサイズを計算（バイト）
        file_size = len(content.encode("utf-8"))

        return (file_path, s3_key, file_size)

    async def read_artifact(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> str:
        """アーティファクトを読み込み"""
        return await self.storage.read(file_path=file_path, s3_key=s3_key)

    async def delete_artifact(
        self,
        file_path: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> bool:
        """アーティファクトを削除"""
        return await self.storage.delete(file_path=file_path, s3_key=s3_key)

    @staticmethod
    def guess_mime_type(filename: str) -> Optional[str]:
        """ファイル名からMIMEタイプを推定"""
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type

    @staticmethod
    def detect_artifact_type(filename: str, mime_type: Optional[str] = None) -> str:
        """
        ファイル名とMIMEタイプからアーティファクトタイプを判定

        Returns:
            "file" | "code" | "notebook" | "image" | "document"
        """
        if not mime_type:
            mime_type = ArtifactStorageService.guess_mime_type(filename)

        # 拡張子ベースの判定
        suffix = Path(filename).suffix.lower()

        if suffix in [".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c", ".go", ".rs", ".rb", ".php"]:
            return "code"
        elif suffix == ".ipynb":
            return "notebook"
        elif suffix in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]:
            return "image"
        elif suffix in [".pdf", ".doc", ".docx", ".txt", ".md"]:
            return "document"
        else:
            return "file"
