# Claude Multi-Agent Backend

AWS Bedrock + Claude Agent SDK + Amazon Bedrock AgentCore Runtime を利用したマルチテナント対応AIエージェントシステム

## 概要

Claude Agent SDKを活用したエージェント実行バックエンドシステムです。
Amazon Bedrock AgentCore Runtime 上でエージェントをFirecracker microVM内で実行し、マルチテナント対応、Agent Skills管理、MCPサーバー連携などの機能を提供します。

## 主要機能

| 機能 | 説明 |
|------|------|
| **テナント管理** | テナントごとの設定（システムプロンプト、デフォルトモデル）管理 |
| **モデル管理** | AWS Bedrockで利用可能なモデルの定義と料金管理 |
| **会話管理** | 会話の作成・継続・アーカイブ |
| **エージェント実行** | Server-Sent Events (SSE) によるストリーミング実行 |
| **Agent Skills** | ファイルシステムベースのSkills管理 |
| **MCPサーバー** | Model Context Protocolサーバーとの連携 |
| **使用状況監視** | トークン使用量とコストのレポート |
| **S3ワークスペース** | 会話ごとの独立したファイル空間（Amazon S3ベース） |

## アーキテクチャ

```
┌──────────────────────────────────────────────────────────────────┐
│  クライアント (フロントエンド)                                       │
└──────────────────────────────────────────────────────────────────┘
                                │ SSE
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI Backend (ECS / EC2 / Lambda)                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ ミドルウェア: トレーシング, API認証, CORS, セキュリティヘッダー  │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ AgentCoreClient (boto3 bedrock-agentcore)                  │  │
│  │ ├── invoke_agent_runtime API                               │  │
│  │ └── runtimeSessionId によるコンテナ親和性                     │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
        │                │                              │
        │ boto3          │ invoke_agent_runtime          │ S3
        ▼                ▼                              ▼
┌──────────┐  ┌──────────────────────────┐      ┌──────────┐
│PostgreSQL│  │ AgentCore Runtime         │      │ Amazon S3│
└──────────┘  │ (Firecracker microVM)     │      └──────────┘
              │  ┌────────────────────┐  │
              │  │ workspace_agent    │  │
              │  │ ├── Claude Agent SDK│  │
              │  │ ├── S3 File Sync   │  │
              │  │ └── /workspace     │  │
              │  └────────────────────┘  │
              │  POST /invocations       │
              │  GET  /ping              │
              └──────────────────────────┘
```

### 実行フロー

1. クライアントが `POST /stream` でリクエスト
2. バックエンドが `invoke_agent_runtime` で AgentCore を呼び出し
3. AgentCore が Firecracker microVM でコンテナを起動
4. コンテナ内で S3 からワークスペースを復元 → SDK 実行 → S3 に同期
5. SSE イベントがリアルタイムでクライアントに中継
6. `runtimeSessionId` でセッション親和性を維持（同一microVMにルーティング）

## 技術スタック

- **言語**: Python 3.11+
- **フレームワーク**: FastAPI
- **データベース**: PostgreSQL (asyncpg)
- **ORM**: SQLAlchemy 2.0
- **マイグレーション**: Alembic
- **AI SDK**: Claude Agent SDK
- **AI基盤**: AWS Bedrock
- **コンテナ実行**: Amazon Bedrock AgentCore Runtime
- **ファイルストレージ**: Amazon S3

## ディレクトリ構成

```
app/
├── core/                  # アプリケーションコア
│   ├── app_factory.py     # アプリケーションファクトリ
│   ├── lifespan.py        # ライフサイクル管理
│   └── metrics_endpoint.py # Prometheusメトリクス
├── api/                   # APIエンドポイント
│   ├── dependencies.py    # 共通依存性注入
│   ├── health.py          # ヘルスチェックAPI
│   ├── conversations/     # 会話API
│   └── ...
├── services/              # ビジネスロジック
│   ├── agentcore_client.py     # AgentCore Runtime クライアント
│   ├── execute_service.py      # エージェント実行
│   ├── mcp_config_builder.py   # MCP設定構築
│   ├── event_translator.py     # SSEイベント変換
│   └── workspace/              # S3ワークスペース
├── infrastructure/        # メトリクス、監査ログ
├── middleware/            # 認証、セキュリティヘッダー
├── models/                # SQLAlchemy ORMモデル
└── schemas/               # Pydanticスキーマ
workspace_agent/           # AgentCoreコンテナ内エージェント
├── main.py                # POST /invocations, GET /ping
├── sdk_client.py          # Claude Agent SDK クライアント
├── agent_file_sync.py     # S3ファイル同期
└── models.py              # リクエストモデル
workspace-base/            # コンテナイメージ
├── Dockerfile             # ARM64, port 8080
└── workspace-requirements.txt
```

## AWS環境構築

### 前提条件

- AWS CLI設定済み
- S3バケット（ワークスペース用）
- PostgreSQL（RDS推奨）
- AgentCore Runtime作成済み

### 1. AgentCore Runtime の作成

```bash
# コンテナイメージをビルド・プッシュ
docker build --platform linux/arm64 -t workspace-base:latest -f workspace-base/Dockerfile .

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-west-2.amazonaws.com
docker tag workspace-base:latest <account>.dkr.ecr.us-west-2.amazonaws.com/workspace-agent:latest
docker push <account>.dkr.ecr.us-west-2.amazonaws.com/workspace-agent:latest

# AgentCore Runtime を作成（AWS Console または CLI）
# ポート: 8080, ヘルスチェック: GET /ping
```

### 2. IAMポリシー

バックエンドのIAMロールに以下の権限が必要です:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock-agentcore:InvokeAgentRuntime"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:HeadObject"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket-name",
        "arn:aws:s3:::your-bucket-name/*"
      ]
    }
  ]
}
```

### 3. 環境変数

```bash
# 必須
AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/xxxx
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
S3_BUCKET_NAME=your-workspace-bucket
API_KEYS=your-secure-api-key
AWS_REGION=us-west-2

# オプション
APP_ENV=production
LOG_LEVEL=WARNING
```

## ローカル開発

### Docker Compose（推奨）

```bash
# 1. 環境変数を設定
cp .env.example .env
# .env を編集: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME, AGENTCORE_RUNTIME_ARN

# 2. バックエンドを起動
docker-compose up -d --build

# 3. 起動確認
curl http://localhost:8000/health
```

DBマイグレーションはコンテナ起動時に自動実行されます。

### ローカル開発（Dockerなし）

```bash
# 仮想環境を作成
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# PostgreSQLを起動（別途必要）

# アプリケーション起動
uvicorn app.main:app --reload
```

## セキュリティ

### 認証

```bash
# X-API-Key ヘッダー
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/tenants

# Authorization ヘッダー
curl -H "Authorization: Bearer your-api-key" http://localhost:8000/api/tenants
```

### 識別ヘッダー

```bash
# AI実行系API（一般ユーザー向け）
curl -H "X-API-Key: key" -H "X-Tenant-ID: tenant-123" -H "X-User-ID: user-456" \
  http://localhost:8000/api/tenants/xxx/conversations

# 管理系API（管理者向け）
curl -H "X-API-Key: key" -H "X-Admin-ID: admin-789" \
  http://localhost:8000/api/tenants
```

## API概要

### ヘルスチェック

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/health` | 詳細ヘルスチェック（DB, S3接続確認） |
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness probe |
| GET | `/metrics` | Prometheusメトリクス |

### 主要エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/tenants` | テナント作成 |
| POST | `/api/tenants/{id}/conversations` | 会話作成 |
| POST | `/api/tenants/{id}/conversations/{cid}/stream` | ストリーミング実行 |
| GET | `/api/tenants/{id}/conversations` | 会話一覧 |
| POST | `/api/tenants/{id}/skills` | Skillアップロード |
| POST | `/api/tenants/{id}/mcp-servers` | MCPサーバー登録 |
| GET | `/api/tenants/{id}/usage` | 使用状況取得 |

### 基本フロー

```
1. POST /api/tenants              → テナントを作成
2. POST /api/models               → モデルを登録
3. POST /api/tenants/{id}/conversations  → 会話を作成
4. POST /api/tenants/{id}/conversations/{cid}/stream → ストリーミング実行
```

## ドキュメント

- [API仕様書](docs/api-specification/) - エンドポイントの詳細仕様
- [使い方ガイド](docs/usage-guide.md) - 基本的な使い方
- [Skills・MCPセットアップ](docs/container-skills-mcp-setup.md) - Skills/MCPサーバーの設定

## ライセンス

開発中
