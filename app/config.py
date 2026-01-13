"""
アプリケーション設定
環境変数からの読み込みと設定値の管理を行う
"""
from functools import lru_cache
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """アプリケーション設定クラス"""

    # ============================================
    # データベース設定
    # ============================================
    database_url: str = "postgresql+asyncpg://aiagent:aiagent_password@localhost:5432/aiagent_db"

    # ============================================
    # AWS Bedrock設定
    # ============================================
    # Bedrockを使用するかどうか
    claude_code_use_bedrock: str = "1"

    # AWSリージョン
    aws_region: str = "us-west-2"

    # AWS認証情報（開発環境用、本番ではIAMロールを使用）
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_session_token: Optional[str] = None

    # デフォルトモデル
    anthropic_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    anthropic_small_fast_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    # ============================================
    # アプリケーション設定
    # ============================================
    app_env: str = "development"
    app_port: int = 8000
    log_level: str = "INFO"

    # Skills保存ベースパス
    skills_base_path: str = "/skills"

    # ============================================
    # S3ワークスペース設定
    # ============================================
    s3_bucket_name: str = ""
    s3_workspace_prefix: str = "workspaces/"

    # ローカルワークスペース一時ディレクトリ
    # セキュリティ向上のため、アプリケーション専用ディレクトリを使用
    workspace_temp_dir: str = "/var/lib/aiagent/workspaces"

    # ============================================
    # セキュリティ設定
    # ============================================
    # CORS許可オリジン
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # APIレート制限
    rate_limit_requests: int = 100
    rate_limit_period: int = 60

    @field_validator("cors_origins")
    @classmethod
    def parse_cors_origins(cls, v: str) -> str:
        """CORS originsのバリデーション"""
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS originsをリストとして取得"""
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def is_production(self) -> bool:
        """本番環境かどうか"""
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        """開発環境かどうか"""
        return self.app_env == "development"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # 追加の環境変数を無視
    )


@lru_cache()
def get_settings() -> Settings:
    """設定インスタンスを取得（キャッシュ付き）"""
    return Settings()
