# 6. Backend 環境変数設定

Backend（FastAPI アプリケーション）に設定する環境変数の完全一覧。
ECS モード固有の設定と、モード共通の設定に分けて記載する。

## 6.1 ECS モード必須設定

以下は `CONTAINER_MANAGER_TYPE=ecs` を設定した場合に**必須**の環境変数。

| 環境変数 | 例 | 説明 |
|----------|-----|------|
| `CONTAINER_MANAGER_TYPE` | `ecs` | ECS モードを有効化 |
| `ECS_CLUSTER` | `ai-agent-cluster` | ECS クラスター名 |
| `ECS_TASK_DEFINITION` | `workspace-agent` | タスク定義（family名、family:rev、または ARN） |
| `ECS_SUBNETS` | `subnet-aaa,subnet-bbb` | ECS タスクを配置するサブネット ID（カンマ区切り） |
| `ECS_SECURITY_GROUPS` | `sg-xxxxx` | ECS タスクに適用するセキュリティグループ（カンマ区切り） |
| `AWS_REGION` | `ap-northeast-1` | AWS リージョン |

## 6.2 ECS モードオプション設定

| 環境変数 | デフォルト | 説明 |
|----------|----------|------|
| `ECS_CAPACITY_PROVIDER` | (空) | Capacity Provider 名。空の場合は Fargate を使用 |
| `ECS_AGENT_PORT` | `9000` | workspace-agent の HTTP ポート |
| `ECS_PROXY_ADMIN_PORT` | `8081` | proxy-sidecar の Admin HTTP ポート |
| `ECS_RUN_TASK_CONCURRENCY` | `10` | RunTask API の同時呼び出し上限（WarmPool 補充時のスロットリング防止） |
| `ECS_WARM_POOL_MIN_SIZE` | `50` | WarmPool 最小サイズ（プリヒート目標） |
| `ECS_WARM_POOL_MAX_SIZE` | `120` | WarmPool 最大サイズ |

## 6.3 データベース設定

| 環境変数 | 例 | 説明 |
|----------|-----|------|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@rds-endpoint:5432/aiagent` | RDS 接続 URL |
| `DB_POOL_SIZE` | `20` | コネクションプールサイズ |
| `DB_MAX_OVERFLOW` | `40` | プール超過時の最大コネクション数 |

## 6.4 Redis 設定

| 環境変数 | 例 | 説明 |
|----------|-----|------|
| `REDIS_URL` | `redis://ai-agent-redis.xxxxx.ng.0001.apne1.cache.amazonaws.com:6379/0` | ElastiCache エンドポイント |
| `REDIS_PASSWORD` | (シークレット) | Redis AUTH トークン（**本番必須**） |
| `REDIS_MAX_CONNECTIONS` | `20` | Redis コネクションプールサイズ |

## 6.5 AWS Bedrock 設定

| 環境変数 | デフォルト | 説明 |
|----------|----------|------|
| `CLAUDE_CODE_USE_BEDROCK` | `1` | Bedrock モード有効化 |
| `AWS_ACCESS_KEY_ID` | - | AWS 認証（IAM ロール使用時は不要） |
| `AWS_SECRET_ACCESS_KEY` | - | AWS 認証（IAM ロール使用時は不要） |
| `AWS_SESSION_TOKEN` | - | STS 一時認証（通常不要） |
| `ANTHROPIC_SONNET_MODEL` | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | メインモデル ID |
| `ANTHROPIC_HAIKU_MODEL` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | 軽量モデル ID |

## 6.6 S3 ワークスペース設定

| 環境変数 | デフォルト | 説明 |
|----------|----------|------|
| `S3_BUCKET_NAME` | (空) | ワークスペース用 S3 バケット名 |
| `S3_WORKSPACE_PREFIX` | `workspaces/` | ワークスペースファイルのプレフィックス |
| `S3_SKILLS_PREFIX` | `skills/` | Skills バックアップのプレフィックス |
| `S3_SKILLS_BACKUP_ENABLED` | `true` | Skills の S3 バックアップ有効化 |
| `S3_CHUNK_SIZE` | `8388608` | S3 マルチパートアップロードのチャンクサイズ（8MB） |

## 6.7 アプリケーション設定

| 環境変数 | 本番推奨値 | 説明 |
|----------|----------|------|
| `APP_ENV` | `production` | `production` でバリデーション強化 |
| `LOG_LEVEL` | `INFO` | ログレベル |
| `API_KEYS` | (シークレット) | API 認証キー（16文字以上、カンマ区切りで複数指定可能。**本番必須**） |
| `CORS_ORIGINS` | `https://your-domain.com` | フロントエンドの URL（**localhost 禁止**） |
| `HSTS_ENABLED` | `true` | HTTPS 終端がある場合は `true` |

## 6.8 コンテナ隔離設定（モード共通）

| 環境変数 | デフォルト | 説明 |
|----------|----------|------|
| `CONTAINER_IMAGE` | `workspace-base:latest` | ワークスペースイメージ（Docker モード用。ECS ではタスク定義が優先） |
| `CONTAINER_INACTIVE_TTL` | `3600` | 非アクティブコンテナの TTL（秒） |
| `CONTAINER_ABSOLUTE_TTL` | `28800` | コンテナの絶対 TTL（秒、8時間） |
| `CONTAINER_EXECUTION_TIMEOUT` | `600` | エージェント実行タイムアウト（秒、10分） |
| `CONTAINER_GRACE_PERIOD` | `30` | グレースフル停止の猶予時間（秒） |
| `CONTAINER_GC_INTERVAL` | `60` | GC ループ間隔（秒） |

## 6.9 Proxy 設定

| 環境変数 | デフォルト | 説明 |
|----------|----------|------|
| `PROXY_DOMAIN_WHITELIST` | (長いリスト) | 許可ドメイン（カンマ区切り） |
| `PROXY_LOG_ALL_REQUESTS` | `true` | 全リクエストのログ出力 |

## 6.10 本番環境 .env ファイルのテンプレート

```bash
# ===== ECS モード =====
CONTAINER_MANAGER_TYPE=ecs
ECS_CLUSTER=ai-agent-cluster
ECS_TASK_DEFINITION=workspace-agent
ECS_SUBNETS=subnet-xxxxxxxxxxxxxxxxx,subnet-yyyyyyyyyyyyyyyyy
ECS_SECURITY_GROUPS=sg-xxxxxxxxxxxxxxxxx
ECS_WARM_POOL_MIN_SIZE=50
ECS_WARM_POOL_MAX_SIZE=120
ECS_RUN_TASK_CONCURRENCY=10

# ===== AWS =====
AWS_REGION=ap-northeast-1
CLAUDE_CODE_USE_BEDROCK=1
# IAM ロール使用時は以下は不要
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=

# ===== データベース =====
DATABASE_URL=postgresql+asyncpg://aiagent:<DB_PASSWORD>@ai-agent-db.xxxxx.ap-northeast-1.rds.amazonaws.com:5432/aiagent

# ===== Redis =====
REDIS_URL=redis://ai-agent-redis.xxxxx.ng.0001.apne1.cache.amazonaws.com:6379/0
REDIS_PASSWORD=<REDIS_AUTH_TOKEN>

# ===== S3 =====
S3_BUCKET_NAME=ai-agent-workspaces-123456789012
S3_SKILLS_BACKUP_ENABLED=true

# ===== アプリケーション =====
APP_ENV=production
LOG_LEVEL=INFO
API_KEYS=<YOUR_API_KEY_1>,<YOUR_API_KEY_2>
CORS_ORIGINS=https://your-app.example.com

# ===== セキュリティ =====
HSTS_ENABLED=true

# ===== モデル =====
ANTHROPIC_SONNET_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
ANTHROPIC_HAIKU_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0

# ===== Proxy =====
PROXY_DOMAIN_WHITELIST=pypi.org,files.pythonhosted.org,registry.npmjs.org,api.anthropic.com,bedrock-runtime.ap-northeast-1.amazonaws.com
```

## 6.11 設定の検証

Backend 起動時に `APP_ENV=production` の場合、以下が自動検証される:

- `API_KEYS` が設定されていること
- `DATABASE_URL` にデフォルトパスワードが含まれていないこと
- `CORS_ORIGINS` に `localhost` / `127.0.0.1` が含まれていないこと
- `CORS_ORIGINS` にワイルドカード `*` が含まれていないこと
- `REDIS_PASSWORD` が設定されていること

検証に失敗するとアプリケーション起動時にエラーとなる。
