"""
アプリケーション設定
環境変数からの読み込みと設定値の管理を行う
"""
import re
from functools import lru_cache
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """アプリケーション設定クラス"""

    # ============================================
    # データベース設定
    # ============================================
    database_url: str = "postgresql+asyncpg://aiagent:aiagent_password@localhost:5432/aiagent"

    # ============================================
    # Redis設定
    # ============================================
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 20

    @property
    def redis_url_masked(self) -> str:
        """パスワードをマスクしたRedis URL"""
        # redis://:password@host:port/db のパスワード部分をマスク
        return re.sub(r"://:[^@]+@", "://***@", self.redis_url)

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

    # モデル設定（global.プレフィックス = クロスリージョン推論）
    # Sonnetモデル（メインエージェント用）
    anthropic_sonnet_model: str = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    # Haikuモデル（サブエージェント用）
    anthropic_haiku_model: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    # SDKが使用するサブエージェントのデフォルトモデルエイリアス
    claude_code_subagent_model: str = "haiku"

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

    # ファイルアップロード制限
    max_upload_file_size: int = 100 * 1024 * 1024  # 100MB

    # ============================================
    # セキュリティ設定
    # ============================================
    # CORS許可オリジン
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # CORS許可メソッド（カンマ区切り、デフォルトは必要なもののみ）
    cors_methods: str = "GET,POST,PUT,DELETE,OPTIONS"

    # CORS許可ヘッダー（カンマ区切り）
    cors_headers: str = "Content-Type,Authorization,X-API-Key,X-Request-ID,X-Tenant-ID"

    # API認証キー（カンマ区切りで複数指定可能）
    # 空の場合は認証が無効化される（開発環境用）
    api_keys: str = ""

    # レート制限
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_period: int = 60

    # HSTS設定
    hsts_enabled: bool = True
    hsts_max_age: int = 31536000  # 1年

    @field_validator("cors_origins")
    @classmethod
    def parse_cors_origins(cls, v: str) -> str:
        """CORS originsのバリデーション"""
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS originsをリストとして取得"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cors_methods_list(self) -> list[str]:
        """CORS methodsをリストとして取得"""
        return [method.strip() for method in self.cors_methods.split(",") if method.strip()]

    @property
    def cors_headers_list(self) -> list[str]:
        """CORS headersをリストとして取得"""
        return [header.strip() for header in self.cors_headers.split(",") if header.strip()]

    @property
    def api_keys_list(self) -> list[str]:
        """APIキーをリストとして取得"""
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]

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
