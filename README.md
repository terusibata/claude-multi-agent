# Claude Multi-Agent Backend

AWS Bedrock + Claude Agent SDKを利用したマルチテナント対応AIエージェントシステム

## 概要

このプロジェクトは、Claude Agent SDKを活用したエージェント実行バックエンドシステムです。
マルチテナント対応、Agent Skills管理、MCPサーバー連携などの機能を提供します。

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
┌─────────────────────────────────────────────────────────────────────────┐
│  クライアント (フロントエンド)                                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FastAPI Backend                                                        │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ ミドルウェアスタック                                                │  │
│  │ ├── トレーシング (X-Request-ID)                                   │  │
│  │ ├── API認証 (X-API-Key / Bearer Token)                           │  │
│  │ ├── レート制限 (Redis)                                            │  │
│  │ ├── CORS                                                          │  │
│  │ └── セキュリティヘッダー                                           │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ APIエンドポイント                                                  │  │
│  │ /tenants, /models, /conversations, /simple-chats,                 │  │
│  │ /skills, /mcp-servers, /usage, /files                             │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ コンテナオーケストレーション                                        │  │
│  │ ├── Orchestrator  (コンテナ割当・再利用)                           │  │
│  │ ├── WarmPool      (事前起動プール: min 2, max 10)                 │  │
│  │ ├── Lifecycle     (作成・ヘルスチェック・破棄)                      │  │
│  │ └── GC            (TTL/絶対期限による自動回収)                      │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ Credential Injection Proxy                                        │  │
│  │ ├── Reverse Proxy → Bedrock API (SigV4署名注入)                   │  │
│  │ └── Forward Proxy → 外部通信 (ドメインホワイトリスト)               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
        │          │          │                    │
        │          │          │  Unix Socket       │  S3ファイル同期
        │          │          │  (SSE中継)          │  (実行前後)
        │          │          ▼                    │
        │          │  ┌───────────────────────┐    │
        │          │  │ ワークスペースコンテナ   │    │
        │          │  │ (会話ごとに1つ)         │    │
        │          │  │                       │    │
        │          │  │  workspace_agent      │    │
        │          │  │  ├── Claude Agent SDK  │    │
        │          │  │  ├── Skills/MCPツール   │    │
        │          │  │  └── /workspace (作業)  │    │
        │          │  │                       │    │
        │          │  │  セキュリティ: 8層防御    │    │
        │          │  │  (network:none,         │    │
        │          │  │   seccomp, AppArmor,   │    │
        │          │  │   cap-drop ALL, etc.)   │    │
        │          │  └───────────────────────┘    │
        ▼          ▼                               ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│PostgreSQL│  │  Redis   │  │AWS Bedrock│  │ Amazon S3│
│(メタデータ)│  │(ロック等) │  │  (LLM)    │  │(ワークスペース)│
└──────────┘  └──────────┘  └──────────┘  └──────────┘
```

## 技術スタック

- **言語**: Python 3.11+
- **フレームワーク**: FastAPI
- **データベース**: PostgreSQL (asyncpg)
- **キャッシュ/ロック**: Redis
- **ORM**: SQLAlchemy 2.0
- **マイグレーション**: Alembic
- **AI SDK**: Claude Agent SDK
- **AI基盤**: AWS Bedrock

## ディレクトリ構成

```
app/
├── core/                  # アプリケーションコア
│   ├── app_factory.py     # アプリケーションファクトリ (create_app)
│   ├── lifespan.py        # ライフサイクル管理
│   ├── exception_handlers.py # 例外ハンドラ
│   └── metrics_endpoint.py   # Prometheusメトリクス
├── api/                   # APIエンドポイント
│   ├── dependencies.py    # 共通依存性注入 (テナント・モデル検証等)
│   ├── health.py          # ヘルスチェックAPI
│   ├── tenants.py         # テナント管理API
│   ├── models.py          # モデル管理API
│   ├── conversations/     # 会話API (パッケージ)
│   │   ├── router.py      # CRUD エンドポイント
│   │   └── streaming.py   # ストリーミング実行
│   ├── simple_chats/      # シンプルチャットAPI (パッケージ)
│   │   ├── router.py      # CRUD エンドポイント
│   │   └── streaming.py   # ストリーミング実行
│   ├── skills.py          # スキル管理API
│   ├── mcp_servers.py     # MCPサーバー管理API
│   ├── usage.py           # 使用状況API
│   └── workspace.py       # ワークスペースAPI
├── repositories/          # データアクセス層 (Repository パターン)
│   ├── base.py            # ベースリポジトリ
│   ├── tenant_repository.py
│   ├── model_repository.py
│   ├── conversation_repository.py
│   ├── message_log_repository.py
│   ├── usage_repository.py
│   └── simple_chat_repository.py
├── services/              # ビジネスロジック
│   ├── execute_service.py       # エージェント実行 (コンテナ隔離)
│   ├── tenant_service.py        # テナント管理
│   ├── model_service.py         # モデル管理
│   ├── conversation_service.py  # 会話管理
│   ├── message_log_service.py   # メッセージログ管理
│   ├── usage_service.py         # 使用量管理
│   ├── simple_chat_service.py   # シンプルチャット管理
│   ├── skill_service.py         # スキル管理
│   ├── mcp_server_service.py    # MCPサーバー管理
│   ├── openapi_mcp_service.py   # OpenAPI→MCP変換
│   ├── bedrock_client.py        # Bedrock API クライアント
│   ├── aws_config.py            # AWS設定
│   ├── workspace_service.py     # ワークスペース操作
│   ├── container/               # コンテナ管理
│   │   ├── orchestrator.py      # コンテナオーケストレーター
│   │   ├── lifecycle.py         # コンテナライフサイクル
│   │   ├── warm_pool.py         # WarmPoolマネージャー
│   │   ├── gc.py                # コンテナGC (TTL/絶対期限)
│   │   ├── config.py            # コンテナ作成設定 (セキュリティ制御)
│   │   └── models.py            # コンテナデータモデル
│   ├── proxy/                   # Credential Injection Proxy
│   │   ├── credential_proxy.py  # Reverse/Forward Proxy (Unix Socket)
│   │   ├── sigv4.py             # AWS SigV4 リクエスト署名
│   │   ├── domain_whitelist.py  # ドメインホワイトリスト
│   │   └── dns_cache.py         # DNSキャッシュ
│   ├── builtin_tools/           # 組み込みツール
│   │   ├── definitions.py       # ツール定義
│   │   ├── file_presentation.py # ファイル表示
│   │   └── server.py            # ツールサーバー
│   └── workspace/               # ワークスペースインフラ
│       ├── s3_storage.py        # S3ストレージバックエンド
│       ├── file_sync.py         # ファイル同期 (S3↔コンテナ)
│       ├── file_processors.py   # ファイル処理ディスパッチ
│       ├── context_builder.py   # ファイルコンテキスト構築
│       └── file_tools/          # ファイル種別プロセッサ
│           ├── pdf_tools.py     # PDF処理
│           ├── excel_tools.py   # Excel処理
│           ├── word_tools.py    # Word処理
│           ├── pptx_tools.py    # PowerPoint処理
│           ├── image_tools.py   # 画像処理
│           ├── registry.py      # プロセッサ登録
│           └── utils.py         # 共通ユーティリティ
├── infrastructure/        # インフラストラクチャ層
│   ├── redis.py           # Redis接続管理
│   ├── distributed_lock.py # 分散ロック
│   ├── shutdown.py        # グレースフルシャットダウン
│   ├── retry.py           # リトライユーティリティ
│   ├── metrics.py         # Prometheusメトリクス
│   └── audit_log.py       # 監査ログ
├── middleware/            # ミドルウェア
│   ├── auth.py            # API認証
│   ├── rate_limit.py      # レート制限
│   ├── security_headers.py # セキュリティヘッダー
│   └── tracing.py         # リクエストトレーシング
├── models/                # SQLAlchemy ORMモデル
├── schemas/               # Pydantic リクエスト/レスポンススキーマ
├── utils/                 # ユーティリティ (ストリーミング, セキュリティ, エラー処理)
├── config.py              # 設定管理
├── database.py            # データベース接続
└── main.py                # エントリーポイント (create_app呼び出し)
workspace-base/            # コンテナベースイメージ
├── Dockerfile             # Python 3.11 + Node.js 20
├── entrypoint.sh          # エントリーポイント (socat起動等)
└── workspace-requirements.txt
workspace_agent/           # コンテナ内エージェントサーバー
├── main.py                # FastAPI (Unix Socket) エントリーポイント
├── sdk_client.py          # Claude Agent SDK クライアント
└── models.py              # リクエスト/レスポンスモデル
deployment/                # デプロイメント設定
├── docker/                # Docker デーモン設定 (userns-remap)
├── seccomp/               # seccomp プロファイル (システムコール制限)
├── apparmor/              # AppArmor プロファイル (ファイルアクセス制限)
└── s3/                    # S3 ライフサイクルポリシー
monitoring/                # 監視設定
├── prometheus/            # Prometheus (メトリクス収集・アラート)
└── grafana/               # Grafana (ダッシュボード)
alembic/                   # DBマイグレーション
docs/                      # ドキュメント
tests/                     # テスト
```

### アーキテクチャパターン

- **Application Factory**: `app/core/app_factory.py` の `create_app()` でアプリケーションを生成
- **Repository パターン**: `app/repositories/` でデータアクセスを抽象化し、サービス層から SQLAlchemy の直接操作を排除
- **依存性注入**: `app/api/dependencies.py` でテナント/モデル検証等の共通ロジックを FastAPI の `Depends()` で注入
- **コンテナ隔離実行**: 会話ごとに Docker コンテナを割り当て、Unix Socket 経由で SSE イベントを中継

## セットアップ

### 前提条件

- Docker & Docker Compose
- 現在のユーザーが `docker` グループに所属していること
- AWS認証情報（Bedrock + S3用）
- S3バケット（ワークスペース用、パブリックアクセスブロック推奨）

### 開発環境の起動

1. 環境変数を設定

```bash
cp .env.example .env
```

`.env` ファイルを編集して以下を設定:

- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`: AWS認証情報
- `S3_BUCKET_NAME`: ワークスペース用S3バケット名
- `DB_PASSWORD`: データベースパスワード（未設定時はデフォルト値 `aiagent_password` を使用）

2. Docker GIDを設定（コンテナ内からDocker Socketにアクセスするために必要）

```bash
echo "DOCKER_GID=$(getent group docker | cut -d: -f3)" >> .env
```

3. ワークスペース用ソケットディレクトリを作成

```bash
sudo mkdir -p /var/run/workspace-sockets
sudo chown 1000:1000 /var/run/workspace-sockets
```

4. ワークスペースコンテナのベースイメージをビルド

```bash
docker build -t workspace-base:latest -f workspace-base/Dockerfile .
```

5. バックエンドを起動

```bash
docker-compose up -d --build
```

DBマイグレーションはコンテナ起動時に自動実行されます。

6. 起動確認

```bash
# ログでエラーがないか確認
docker-compose logs backend
```

http://localhost:8000/docs でAPIドキュメントにアクセスできれば起動完了です。

### ローカル開発（Dockerなし）

```bash
# 仮想環境を作成
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# PostgreSQLとRedisを起動（別途必要）

# アプリケーション起動
uvicorn app.main:app --reload
```

## セキュリティ

本システムは内部通信用APIとして設計されていますが、以下のセキュリティ機能を備えています。

### 認証

```bash
# X-API-Key ヘッダー（推奨）
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/tenants

# Authorization ヘッダー
curl -H "Authorization: Bearer your-api-key" http://localhost:8000/api/tenants
```

### 識別ヘッダー

APIの種類に応じて、適切なヘッダーを送信してください。

```bash
# AI実行系API（一般ユーザー向け）- 会話、ワークスペース操作
curl -H "X-API-Key: your-api-key" \
     -H "X-Tenant-ID: tenant-123" \
     -H "X-User-ID: user-456" \
     http://localhost:8000/api/tenants/xxx/conversations

# 管理系API（管理者向け）- テナント、モデル、スキル管理
curl -H "X-API-Key: your-api-key" \
     -H "X-Admin-ID: admin-789" \
     http://localhost:8000/api/tenants
```

### レート制限

AI実行系API（一般ユーザー向け）のみにレート制限が適用されます。管理系APIは対象外です。

### セキュリティヘッダー

すべてのレスポンスにOWASP推奨のセキュリティヘッダーが自動付与されます。

### リクエストトレーシング

すべてのリクエストに `X-Request-ID` が付与され、障害調査時に追跡可能です。

詳細は [セキュリティ設定ガイド](docs/operations/security-config-guide.md) を参照してください。

## API概要

### ヘルスチェック・監視

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/health` | 詳細ヘルスチェック（DB, Redis, S3接続確認） |
| GET | `/health/live` | Kubernetes liveness probe |
| GET | `/health/ready` | Kubernetes readiness probe |
| GET | `/metrics` | Prometheusメトリクス（本番環境では認証必要） |

### テナント管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants` | テナント一覧取得 |
| POST | `/api/tenants` | テナント作成 |
| GET | `/api/tenants/{tenant_id}` | テナント取得 |
| PUT | `/api/tenants/{tenant_id}` | テナント更新 |
| DELETE | `/api/tenants/{tenant_id}` | テナント削除 |

### 会話管理・実行

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/conversations` | 会話一覧取得 |
| POST | `/api/tenants/{tenant_id}/conversations` | 会話作成 |
| GET | `/api/tenants/{tenant_id}/conversations/{id}` | 会話詳細取得 |
| POST | `/api/tenants/{tenant_id}/conversations/{id}/stream` | ストリーミング実行 |
| GET | `/api/tenants/{tenant_id}/conversations/{id}/messages` | メッセージログ取得 |
| DELETE | `/api/tenants/{tenant_id}/conversations/{id}` | 会話削除 |

### モデル管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/models` | モデル一覧取得 |
| GET | `/api/models/{model_id}` | モデル詳細取得 |
| POST | `/api/models` | モデル登録 |
| PUT | `/api/models/{model_id}` | モデル定義更新 |
| PATCH | `/api/models/{model_id}/status` | ステータス変更 |
| DELETE | `/api/models/{model_id}` | モデル削除 |

### シンプルチャット

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/simple-chats` | チャット一覧取得 |
| GET | `/api/tenants/{tenant_id}/simple-chats/{id}` | チャット詳細取得 |
| POST | `/api/tenants/{tenant_id}/simple-chats/stream` | ストリーミング実行 (新規/継続) |
| POST | `/api/tenants/{tenant_id}/simple-chats/{id}/archive` | アーカイブ |
| DELETE | `/api/tenants/{tenant_id}/simple-chats/{id}` | 削除 |

### スキル管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/skills` | Skills一覧取得 |
| GET | `/api/tenants/{tenant_id}/skills/{skill_id}` | Skill詳細取得 |
| POST | `/api/tenants/{tenant_id}/skills` | Skill作成 |
| PUT | `/api/tenants/{tenant_id}/skills/{skill_id}` | Skillメタデータ更新 |
| PUT | `/api/tenants/{tenant_id}/skills/{skill_id}/files` | Skillファイル更新 |
| DELETE | `/api/tenants/{tenant_id}/skills/{skill_id}` | Skill削除 |

### MCPサーバー管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/mcp-servers` | MCPサーバー一覧取得 |
| GET | `/api/tenants/{tenant_id}/mcp-servers/builtin` | ビルトインサーバー一覧 |
| GET | `/api/tenants/{tenant_id}/mcp-servers/{server_id}` | MCPサーバー詳細取得 |
| POST | `/api/tenants/{tenant_id}/mcp-servers` | MCPサーバー登録 |
| PUT | `/api/tenants/{tenant_id}/mcp-servers/{server_id}` | MCPサーバー更新 |
| DELETE | `/api/tenants/{tenant_id}/mcp-servers/{server_id}` | MCPサーバー削除 |

### 使用状況・コスト

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/usage` | 使用状況取得 |
| GET | `/api/tenants/{tenant_id}/usage/users/{user_id}` | ユーザー使用状況取得 |
| GET | `/api/tenants/{tenant_id}/usage/summary` | 使用状況サマリー取得 |
| GET | `/api/tenants/{tenant_id}/cost-report` | コストレポート取得 |
| GET | `/api/tenants/{tenant_id}/tool-logs` | ツール実行ログ取得 |

### ワークスペース（ファイル管理）

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/conversations/{id}/files` | ファイル一覧取得 |
| GET | `/api/tenants/{tenant_id}/conversations/{id}/files/download` | ファイルダウンロード |
| GET | `/api/tenants/{tenant_id}/conversations/{id}/files/presented` | AI作成ファイル一覧 |

### 基本フロー

```
1. POST /api/tenants - テナントを作成
2. POST /api/models - モデルを登録
3. POST /api/tenants/{tenant_id}/conversations - 会話を作成
4. POST /api/tenants/{tenant_id}/conversations/{conversation_id}/stream - ストリーミング実行
```

## 主要な概念

### テナント

テナントはマルチテナント環境における組織単位です。テナントごとに以下を設定できます：
- システムプロンプト（AIの基本的な振る舞い）
- デフォルトモデル

### 会話

会話はユーザーとAIの対話の単位です。会話には以下が含まれます：
- 使用するモデル
- ワークスペースの有効/無効
- メッセージ履歴

### ワークスペース

会話ごとに独立したファイル空間を提供します。ファイルはAmazon S3に保存されます。

## 環境変数

### 基本設定

| 変数名 | 説明 | デフォルト |
|--------|------|----------|
| DATABASE_URL | PostgreSQL接続URL（本番ではデフォルトパスワード禁止） | - |
| REDIS_URL | Redis接続URL | redis://localhost:6379/0 |
| REDIS_PASSWORD | Redis認証パスワード（本番では推奨） | - |
| APP_ENV | 環境（development/production） | development |
| APP_PORT | アプリケーションポート | 8000 |
| LOG_LEVEL | ログレベル | INFO |
| SHUTDOWN_TIMEOUT | グレースフルシャットダウンのタイムアウト（秒） | 30.0 |
| METRICS_ENABLED | Prometheusメトリクスの有効化 | true |

### AWS設定

| 変数名 | 説明 | デフォルト |
|--------|------|----------|
| CLAUDE_CODE_USE_BEDROCK | Bedrock使用フラグ | 1 |
| AWS_REGION | AWSリージョン | us-west-2 |
| AWS_ACCESS_KEY_ID | AWSアクセスキー | - |
| AWS_SECRET_ACCESS_KEY | AWSシークレットキー | - |
| ANTHROPIC_SONNET_MODEL | Sonnetモデル（メインエージェント用） | global.anthropic.claude-sonnet-4-5-20250929-v1:0 |
| ANTHROPIC_HAIKU_MODEL | Haikuモデル（サブエージェント用） | global.anthropic.claude-haiku-4-5-20251001-v1:0 |
| S3_BUCKET_NAME | ワークスペース用S3バケット名 | - |

### セキュリティ設定

| 変数名 | 説明 | デフォルト |
|--------|------|----------|
| API_KEYS | APIキー（カンマ区切り、**本番では必須**） | (空) |
| RATE_LIMIT_ENABLED | レート制限の有効化 | true |
| RATE_LIMIT_REQUESTS | ウィンドウあたりのリクエスト数 | 100 |
| RATE_LIMIT_PERIOD | ウィンドウサイズ（秒） | 60 |
| CORS_ORIGINS | CORS許可オリジン（カンマ区切り） | http://localhost:3000,http://localhost:3001 |
| HSTS_ENABLED | HSTSの有効化 | true |

### 本番環境の必須設定

本番環境（`APP_ENV=production`）では以下の設定が必須です：

- **API_KEYS**: 16文字以上のAPIキーを設定（未設定だと起動時エラー）
- **DATABASE_URL**: デフォルトパスワード（`aiagent_password`）は使用禁止
- **REDIS_PASSWORD**: Redis認証の設定を推奨

### AWS IAMポリシー要件

AWS認証情報には **Bedrock** と **S3** の両方の権限が必要です：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
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

## ドキュメント

詳細なドキュメントは `docs/` ディレクトリを参照してください：

- [API仕様書](docs/api-specification/) - エンドポイントの詳細仕様
- [使い方ガイド](docs/usage-guide.md) - 基本的な使い方
- [デプロイメント設定](docs/deployment-guide.md) - コンテナセキュリティ、S3ライフサイクル
- [セキュリティ設定](docs/operations/security-config-guide.md) - 認証、レート制限、セキュリティヘッダー
- [監視ガイド](docs/operations/monitoring-guide.md) - メトリクス、アラート

## ライセンス

開発中
