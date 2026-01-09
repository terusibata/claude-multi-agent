# AIエージェントバックエンド

AWS Bedrock + Claude Agent SDKを利用したマルチテナント対応AIエージェントシステム

## 概要

このプロジェクトは、Claude Agent SDKを活用したエージェント実行バックエンドシステムです。
マルチテナント対応、Agent Skills管理、MCPサーバー連携などの機能を提供します。

## 主要機能

- **モデル管理**: AWS Bedrockで利用可能なモデルの定義と料金管理
- **エージェント実行設定**: テナントごとのエージェント設定管理
- **Agent Skills管理**: ファイルシステムベースのSkills管理
- **MCPサーバー管理**: Model Context Protocolサーバーの設定
- **エージェント実行**: Server-Sent Events (SSE) によるストリーミング実行
- **セッション管理**: 会話履歴とセッションの管理
- **使用状況監視**: トークン使用量とコストのレポート

## 技術スタック

- **言語**: Python 3.11+
- **フレームワーク**: FastAPI
- **データベース**: PostgreSQL (asyncpg)
- **ORM**: SQLAlchemy 2.0
- **マイグレーション**: Alembic
- **AI SDK**: Claude Agent SDK
- **AI基盤**: AWS Bedrock

## ディレクトリ構成

```
app/
├── api/              # APIルーター
├── models/           # SQLAlchemyモデル
├── schemas/          # Pydanticスキーマ
├── services/         # ビジネスロジック
├── utils/            # ユーティリティ
├── config.py         # 設定管理
├── database.py       # データベース接続
└── main.py           # メインアプリケーション
alembic/              # DBマイグレーション
tests/                # テスト
Dockerfile
docker-compose.yml
requirements.txt
```

## セットアップ

### 前提条件

- Docker & Docker Compose
- AWS認証情報（Bedrock用）

### 開発環境の起動

1. 環境変数を設定

```bash
cp .env.example .env
# .envファイルを編集してAWS認証情報を設定
```

2. Dockerコンテナを起動

```bash
docker-compose up -d
```

3. DBマイグレーション実行

```bash
docker-compose exec backend alembic upgrade head
```

4. APIドキュメント確認

http://localhost:8000/docs

### ローカル開発（Dockerなし）

1. 仮想環境を作成

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. PostgreSQLを起動（別途必要）

3. アプリケーション起動

```bash
uvicorn app.main:app --reload
```

## API概要

### 管理系API

- `GET /api/models` - モデル一覧取得
- `POST /api/models` - モデル定義作成
- `GET /api/tenants/{tenant_id}/agent-configs` - エージェント設定一覧
- `POST /api/tenants/{tenant_id}/agent-configs` - エージェント設定作成
- `GET /api/tenants/{tenant_id}/skills` - Skills一覧
- `POST /api/tenants/{tenant_id}/skills` - Skillアップロード
- `GET /api/tenants/{tenant_id}/mcp-servers` - MCPサーバー一覧

### 実行系API

- `POST /api/tenants/{tenant_id}/execute` - エージェント実行（SSE）

### 履歴API

- `GET /api/tenants/{tenant_id}/sessions` - セッション一覧
- `GET /api/tenants/{tenant_id}/sessions/{id}/display` - 表示用キャッシュ
- `GET /api/tenants/{tenant_id}/usage` - 使用状況

## 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|----------|
| DATABASE_URL | PostgreSQL接続URL | - |
| CLAUDE_CODE_USE_BEDROCK | Bedrock使用フラグ | 1 |
| AWS_REGION | AWSリージョン | us-west-2 |
| AWS_ACCESS_KEY_ID | AWSアクセスキー | - |
| AWS_SECRET_ACCESS_KEY | AWSシークレットキー | - |
| APP_ENV | 環境 | development |
| SKILLS_BASE_PATH | Skills保存パス | /skills |

## ライセンス

開発中
