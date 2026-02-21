"""
アプリケーション設定
環境変数からの読み込みと設定値の管理を行う
"""
import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """アプリケーション設定クラス"""

    # ============================================
    # データベース設定
    # ============================================
    database_url: str = "postgresql+asyncpg://aiagent:aiagent_password@localhost:5432/aiagent"

    # コネクションプール設定
    db_pool_size: int = 20
    db_max_overflow: int = 40
    db_pool_timeout: int = 30  # 秒
    db_pool_recycle: int = 3600  # 1時間
    db_connect_timeout: int = 10  # 秒
    db_command_timeout: int = 60  # 秒

    # ============================================
    # Redis設定
    # ============================================
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 20
    redis_socket_timeout: float = 5.0
    redis_socket_connect_timeout: float = 5.0
    # Redis認証（本番環境では設定必須）
    redis_password: str | None = None

    @property
    def redis_url_with_auth(self) -> str:
        """認証情報付きRedis URL"""
        if self.redis_password:
            # redis://host:port/db -> redis://:password@host:port/db
            if "://:@" not in self.redis_url and "://:" not in self.redis_url:
                return self.redis_url.replace("://", f"://:{self.redis_password}@")
        return self.redis_url

    @property
    def redis_url_masked(self) -> str:
        """パスワードをマスクしたRedis URL"""
        return re.sub(r"://:[^@]+@", "://***@", self.redis_url_with_auth)

    # ============================================
    # AWS Bedrock設定
    # ============================================
    claude_code_use_bedrock: str = "1"
    aws_region: str = "us-west-2"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None

    # Bedrockリトライ設定
    bedrock_max_retries: int = 3
    bedrock_retry_base_delay: float = 1.0
    bedrock_retry_max_delay: float = 10.0

    # モデル設定
    anthropic_sonnet_model: str = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    anthropic_haiku_model: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    claude_code_subagent_model: str = "haiku"

    # ============================================
    # アプリケーション設定
    # ============================================
    app_env: str = "development"
    app_port: int = 8000
    log_level: str = "INFO"

    # シャットダウン設定
    shutdown_timeout: float = 30.0

    # Skills保存ベースパス
    skills_base_path: str = "/skills"

    # ============================================
    # S3ワークスペース設定
    # ============================================
    s3_bucket_name: str = ""
    s3_workspace_prefix: str = "workspaces/"
    workspace_temp_dir: str = "/var/lib/aiagent/workspaces"

    # S3チャンク設定（メモリ最適化）
    s3_chunk_size: int = 8 * 1024 * 1024  # 8MB

    # S3 Skillsバックアップ設定
    s3_skills_prefix: str = "skills/"
    s3_skills_backup_enabled: bool = True

    # ============================================
    # ファイルアップロード制限
    # ============================================
    max_upload_file_size: int = 100 * 1024 * 1024  # 100MB
    max_image_file_size: int = 5 * 1024 * 1024  # 5MB
    max_pdf_file_size: int = 20 * 1024 * 1024  # 20MB
    max_office_file_size: int = 30 * 1024 * 1024  # 30MB
    max_text_file_size: int = 5 * 1024 * 1024  # 5MB

    # ============================================
    # セキュリティ設定
    # ============================================
    cors_origins: str = "http://localhost:3000,http://localhost:3001"
    cors_methods: str = "GET,POST,PUT,DELETE,OPTIONS"
    cors_headers: str = "Content-Type,Authorization,X-API-Key,X-Request-ID,X-Tenant-ID,X-User-ID,X-Admin-ID"

    # API認証キー（本番環境では必須）
    api_keys: str = ""

    # レート制限
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_period: int = 60

    # HSTS設定（プライベートネットワーク内のHTTP通信では無効にすること）
    # デフォルトはFalse。HTTPS終端がある環境では HSTS_ENABLED=true を設定
    hsts_enabled: bool = False
    hsts_max_age: int = 31536000  # 1年

    # ============================================
    # コンテナ隔離設定
    # ============================================
    container_image: str = "workspace-base:latest"
    container_cpu_quota: int = 200000  # 2 cores (CpuPeriod=100000)
    container_memory_limit: int = 2 * 1024 ** 3  # 2GB
    container_pids_limit: int = 256  # SDK CLIサブプロセス + socat を考慮
    container_disk_limit: str = ""  # ディスク制限（本番: "5G"、xfs+pquota必須）
    container_inactive_ttl: int = 3600  # 60分
    container_absolute_ttl: int = 28800  # 8時間
    container_execution_timeout: int = 600  # 10分
    container_grace_period: int = 30  # 秒

    # SSEアイドルタイムアウト（秒）
    # container_execution_timeout より大きく設定し、httpxタイムアウトが先に発火するようにする。
    # このタイムアウトは「httpx完了後の後処理がスタックした場合」の安全ネット。
    # 階層: container_execution_timeout(600s) < event_timeout(720s) < Lock TTL(900s)
    event_timeout: int = 720  # 12分
    container_healthcheck_interval: int = 30  # 秒
    container_gc_interval: int = 60  # GCループ間隔（秒）

    # ============================================
    # コンテナマネージャー設定
    # ============================================
    container_manager_type: str = "docker"  # "docker" or "ecs"

    # ============================================
    # WarmPool設定
    # ============================================
    warm_pool_min_size: int = 2
    warm_pool_max_size: int = 10
    warm_pool_ttl: int = 1800  # 30分

    # ECS用WarmPool設定（container_manager_type=ecsの場合に使用）
    ecs_warm_pool_min_size: int = 50
    ecs_warm_pool_max_size: int = 120

    # ============================================
    # ECS設定（container_manager_type=ecsの場合に使用）
    # ============================================
    ecs_cluster: str = ""
    ecs_task_definition: str = ""
    ecs_subnets: str = ""  # カンマ区切り
    ecs_security_groups: str = ""  # カンマ区切り
    ecs_capacity_provider: str = ""
    ecs_agent_port: int = 9000
    ecs_proxy_admin_port: int = 8081
    ecs_run_task_concurrency: int = 10  # RunTask API同時呼び出し上限

    @property
    def ecs_subnets_list(self) -> list[str]:
        """ECSサブネットをリストとして取得"""
        return [s.strip() for s in self.ecs_subnets.split(",") if s.strip()]

    @property
    def ecs_security_groups_list(self) -> list[str]:
        """ECSセキュリティグループをリストとして取得"""
        return [s.strip() for s in self.ecs_security_groups.split(",") if s.strip()]

    # ============================================
    # Proxy設定
    # ============================================
    proxy_domain_whitelist: str = "pypi.org,files.pythonhosted.org,registry.npmjs.org,api.anthropic.com,bedrock-runtime.us-east-1.amazonaws.com,bedrock-runtime.us-west-2.amazonaws.com,bedrock-runtime.ap-northeast-1.amazonaws.com"
    proxy_log_all_requests: bool = True

    # ============================================
    # セキュリティ強化設定 (Phase 2/5)
    # ============================================
    seccomp_profile_path: str = "deployment/seccomp/workspace-seccomp.json"
    userns_remap_enabled: bool = False  # userns-remap有効化（Docker daemon設定と連動）
    apparmor_profile_name: str = ""  # AppArmorプロファイル名（本番: "workspace-container"、要ホスト側ロード）

    # ============================================
    # Docker設定
    # ============================================
    docker_socket_path: str = "unix:///var/run/docker.sock"
    workspace_socket_base_path: str = "/var/run/workspace-sockets"
    # Docker-in-Docker環境でホスト側のパスが異なる場合に指定
    # 未設定時は workspace_socket_base_path と同じ値を使用
    workspace_socket_host_path: str = ""

    # ============================================
    # メトリクス設定
    # ============================================
    metrics_enabled: bool = True

    # ============================================
    # Uvicorn設定
    # ============================================
    uvicorn_workers: int = 1
    uvicorn_timeout_keep_alive: int = 65
    uvicorn_timeout_notify: int = 30

    # ============================================
    # バリデーション
    # ============================================

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, v: str) -> str:
        """CORS originsのバリデーション"""
        if not v:
            return v
        for origin in v.split(","):
            origin = origin.strip()
            if origin and origin != "*":
                parsed = urlparse(origin)
                if parsed.scheme not in ("http", "https"):
                    raise ValueError(f"無効なCORSオリジン: {origin}")
        return v

    @field_validator("api_keys")
    @classmethod
    def validate_api_keys_format(cls, v: str) -> str:
        """APIキーの形式をバリデーション"""
        if not v:
            return v
        for key in v.split(","):
            key = key.strip()
            if key and len(key) < 16:
                raise ValueError("APIキーは16文字以上である必要があります")
        return v

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """本番環境の設定を検証"""
        if self.is_production:
            # 本番環境ではAPIキーが必須
            if not self.api_keys_list:
                raise ValueError("本番環境ではAPI_KEYSの設定が必須です")

            # 本番環境ではデフォルトのDBパスワードは禁止
            if "aiagent_password" in self.database_url:
                raise ValueError("本番環境ではデフォルトのデータベースパスワードは使用できません")

            # 本番環境ではlocalhost/127.0.0.1のCORSオリジンを拒否
            if any("localhost" in origin or "127.0.0.1" in origin
                   for origin in self.cors_origins.split(",")):
                raise ValueError(
                    "本番環境ではlocalhost/127.0.0.1のCORSオリジンは使用できません。"
                    "CORS_ORIGINS環境変数を本番URLに設定してください。"
                )

            # ワイルドカードも拒否
            if "*" in self.cors_origins:
                raise ValueError(
                    "本番環境ではワイルドカード(*)のCORSオリジンは使用できません。"
                )

            # 本番環境ではRedisパスワードが必須
            if not self.redis_password:
                raise ValueError(
                    "本番環境ではREDIS_PASSWORDの設定が必須です。"
                )

        return self

    # ============================================
    # プロパティ
    # ============================================

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
    def proxy_domain_whitelist_list(self) -> list[str]:
        """Proxyドメインホワイトリストをリストとして取得"""
        return [d.strip() for d in self.proxy_domain_whitelist.split(",") if d.strip()]

    @property
    def resolved_socket_host_path(self) -> str:
        """コンテナBind mount用のホスト側ソケットパスを取得"""
        return self.workspace_socket_host_path or self.workspace_socket_base_path

    @property
    def is_production(self) -> bool:
        """本番環境かどうか"""
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        """開発環境かどうか"""
        return self.app_env == "development"

    @property
    def log_level_int(self) -> int:
        """ログレベルを数値で取得"""
        import logging
        return getattr(logging, self.log_level.upper(), logging.INFO)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    """設定インスタンスを取得（キャッシュ付き）"""
    return Settings()


def clear_settings_cache() -> None:
    """設定キャッシュをクリア（テスト用）"""
    get_settings.cache_clear()
