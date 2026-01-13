"""
AWS設定管理
AWS Bedrock関連の設定を統合管理
"""
import json
import structlog
from typing import Optional

import boto3

from app.config import get_settings
from app.models.model import Model

settings = get_settings()
logger = structlog.get_logger(__name__)


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
        # セキュリティ上、認証情報の内容はログに出力しない
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

    def build_env_vars(self) -> dict[str, str]:
        """
        Bedrock用環境変数の辞書を構築

        Returns:
            環境変数の辞書
        """
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": self.region,
        }

        # AWS認証情報を追加（設定されている場合のみ）
        if settings.aws_access_key_id and settings.aws_access_key_id.strip():
            env["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id

        if settings.aws_secret_access_key and settings.aws_secret_access_key.strip():
            env["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key

        if self.has_session_token:
            env["AWS_SESSION_TOKEN"] = settings.aws_session_token

        logger.info(
            "Bedrock環境変数構築完了",
            region=env["AWS_REGION"],
            has_access_key="AWS_ACCESS_KEY_ID" in env,
            has_secret_key="AWS_SECRET_ACCESS_KEY" in env,
            has_session_token="AWS_SESSION_TOKEN" in env,
        )

        return env

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


class TitleGenerator:
    """
    セッションタイトル生成クラス

    会話内容からタイトルを自動生成
    """

    DEFAULT_MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    MAX_TITLE_LENGTH = 50
    MAX_USER_INPUT_LENGTH = 200
    MAX_ASSISTANT_RESPONSE_LENGTH = 300

    def __init__(self, aws_config: AWSConfig):
        """
        初期化

        Args:
            aws_config: AWS設定
        """
        self.aws_config = aws_config

    def generate(
        self,
        user_input: str,
        assistant_response: str,
        model_region: Optional[str] = None,
    ) -> str:
        """
        会話からタイトルを生成

        Args:
            user_input: ユーザー入力
            assistant_response: アシスタント応答
            model_region: AWSリージョン

        Returns:
            生成されたタイトル（最大50文字）
        """
        try:
            bedrock_runtime = self.aws_config.create_bedrock_client(
                region=model_region
            )

            prompt = self._build_prompt(user_input, assistant_response)

            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}],
            }

            response = bedrock_runtime.invoke_model(
                modelId=self.DEFAULT_MODEL_ID,
                body=json.dumps(request_body),
            )

            response_body = json.loads(response["body"].read())
            title = response_body["content"][0]["text"].strip()

            # 最大文字数に制限
            if len(title) > self.MAX_TITLE_LENGTH:
                title = title[:self.MAX_TITLE_LENGTH]

            logger.info("タイトル生成成功", title=title)
            return title

        except Exception as e:
            logger.warning("タイトル生成失敗、デフォルトタイトル使用", error=str(e))
            # 失敗した場合はユーザー入力の最初の部分を使用
            return self._fallback_title(user_input)

    def _build_prompt(self, user_input: str, assistant_response: str) -> str:
        """タイトル生成用のプロンプトを構築"""
        truncated_input = user_input[:self.MAX_USER_INPUT_LENGTH]
        truncated_response = assistant_response[:self.MAX_ASSISTANT_RESPONSE_LENGTH]

        return f"""以下の会話から、短く簡潔な日本語のタイトルを生成してください。
タイトルは20文字以内にしてください。

ユーザー入力:
{truncated_input}

アシスタント応答:
{truncated_response}

タイトルのみを出力してください。説明は不要です。"""

    def _fallback_title(self, user_input: str) -> str:
        """フォールバックタイトルを生成"""
        if user_input:
            return user_input[:self.MAX_TITLE_LENGTH]
        return "新しいチャット"
