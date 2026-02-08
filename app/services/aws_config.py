"""
AWS設定管理
AWS Bedrock関連の設定を統合管理
"""
import json
import re
import structlog
from typing import Optional

import boto3

from app.config import get_settings
from app.models.model import Model

settings = get_settings()
logger = structlog.get_logger(__name__)

# 有効なAWSリージョン形式のパターン（例: us-east-1, ap-northeast-1, eu-west-2）
AWS_REGION_PATTERN = re.compile(r'^[a-z]{2}-[a-z]+-\d+$')


def is_valid_aws_region(region: Optional[str]) -> bool:
    """
    AWSリージョン名が有効な形式かチェック

    Args:
        region: リージョン名

    Returns:
        有効な形式ならTrue
    """
    if not region or not region.strip():
        return False
    return bool(AWS_REGION_PATTERN.match(region.strip()))


class AWSConfig:
    """
    AWS設定クラス

    Bedrock利用に必要な環境変数やクライアント設定を一元管理
    """

    def __init__(self, model: Optional[Model] = None):
        """
        初期化

        Args:
            model: モデル定義（リージョン設定用）
        """
        self.model = model
        self._validate_credentials()

    def _validate_credentials(self) -> None:
        """認証情報の検証とログ出力"""
        has_access_key = bool(settings.aws_access_key_id and settings.aws_access_key_id.strip())
        has_secret_key = bool(settings.aws_secret_access_key and settings.aws_secret_access_key.strip())

        if not has_access_key:
            logger.warning("AWS_ACCESS_KEY_IDが設定されていません")
        else:
            logger.debug("AWS_ACCESS_KEY_ID設定済み")

        if not has_secret_key:
            logger.warning("AWS_SECRET_ACCESS_KEYが設定されていません")

    @property
    def region(self) -> str:
        """AWSリージョンを取得"""
        if self.model and self.model.model_region:
            return self.model.model_region
        return settings.aws_region

    @property
    def has_credentials(self) -> bool:
        """認証情報が設定されているか"""
        return bool(
            settings.aws_access_key_id
            and settings.aws_access_key_id.strip()
            and settings.aws_secret_access_key
            and settings.aws_secret_access_key.strip()
        )

    @property
    def has_session_token(self) -> bool:
        """セッショントークンが設定されているか"""
        return bool(settings.aws_session_token and settings.aws_session_token.strip())

    def create_bedrock_client(self, region: Optional[str] = None):
        """
        Bedrock Runtimeクライアントを作成

        Args:
            region: リージョン（省略時はデフォルト）

        Returns:
            Bedrock Runtimeクライアント
        """
        client_region = region or self.region

        client_args = {
            "service_name": "bedrock-runtime",
            "region_name": client_region,
        }

        if self.has_credentials:
            client_args["aws_access_key_id"] = settings.aws_access_key_id
            client_args["aws_secret_access_key"] = settings.aws_secret_access_key

            if self.has_session_token:
                client_args["aws_session_token"] = settings.aws_session_token

        return boto3.client(**client_args)
