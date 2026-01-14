# 使い方ガイド

Claude Multi-Agent API の使い方ガイドです。

## 目次

- [はじめに](#はじめに)
- [セットアップ](#セットアップ)
- [基本的な使い方](#基本的な使い方)
- [テナント管理](#テナント管理)
- [ワークスペース機能](#ワークスペース機能)
- [MCPサーバー](#mcpサーバー)
- [スキル機能](#スキル機能)
- [使用量管理](#使用量管理)
- [ベストプラクティス](#ベストプラクティス)

## はじめに

Claude Multi-Agent は、Claude Agent SDK を使用したマルチテナント対応のエージェント実行APIです。

### 主な機能

| 機能 | 説明 |
|------|------|
| **マルチテナント** | テナントごとに設定・使用量を分離 |
| **ストリーミング応答** | SSE形式でリアルタイム応答 |
| **ワークスペース** | 会話ごとの独立したファイル空間 |
| **MCP連携** | Model Context Protocolサーバーとの連携 |
| **スキル機能** | カスタム機能の追加 |

## セットアップ

### 環境変数

```bash
# データベース
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbname

# AWS Bedrock & S3
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-west-2
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key

# S3ワークスペース
S3_BUCKET_NAME=your-app-workspaces
S3_WORKSPACE_PREFIX=workspaces/

# アプリケーション
APP_ENV=development
LOG_LEVEL=INFO
SKILLS_BASE_PATH=/skills

# CORS
CORS_ORIGINS=http://localhost:3000
```

**重要**: AWS認証情報には **Bedrock** と **S3** の両方の権限が必要です。

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
        "arn:aws:s3:::your-app-workspaces",
        "arn:aws:s3:::your-app-workspaces/*"
      ]
    }
  ]
}
```

### Docker起動

```bash
docker-compose up -d
```

### マイグレーション

```bash
alembic upgrade head
```

## 基本的な使い方

### 1. モデルの登録

まず、使用するモデルを登録します。

```bash
curl -X POST http://localhost:8000/api/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "claude-sonnet-4",
    "display_name": "Claude Sonnet 4",
    "bedrock_model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "model_region": "us-west-2",
    "input_token_price": "3.00",
    "output_token_price": "15.00"
  }'
```

### 2. テナントの作成

次に、テナントを作成します。

```bash
curl -X POST http://localhost:8000/api/tenants \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-001",
    "system_prompt": "あなたは親切なアシスタントです。",
    "model_id": "claude-sonnet-4"
  }'
```

### 3. 会話の作成

会話を作成します。

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "model_id": "claude-sonnet-4",
    "enable_workspace": false
  }'
```

レスポンス：
```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "status": "active",
  "enable_workspace": false,
  "created_at": "2024-01-01T00:00:00Z"
}
```

### 4. エージェントの実行

作成した会話でストリーミング実行します。

```bash
curl -X POST "http://localhost:8000/api/tenants/tenant-001/conversations/550e8400-uuid/stream" \
  -H "Accept: text/event-stream" \
  -F 'request_data={
    "user_input": "Pythonでソートアルゴリズムを実装してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }'
```

### 5. 会話の継続

同じ会話で続ける場合は、同じエンドポイントを使用します。

```bash
curl -X POST "http://localhost:8000/api/tenants/tenant-001/conversations/550e8400-uuid/stream" \
  -F 'request_data={
    "user_input": "バブルソートも追加してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }'
```

## テナント管理

### テナントの役割

テナントはマルチテナント環境における組織単位です。テナントごとに以下を設定できます：

- **システムプロンプト**: AIの基本的な振る舞いを定義
- **デフォルトモデル**: 会話作成時のデフォルトモデル

### テナントの設定例

```bash
# テナントの更新
curl -X PUT http://localhost:8000/api/tenants/tenant-001 \
  -H "Content-Type: application/json" \
  -d '{
    "system_prompt": "あなたは優秀なソフトウェアエンジニアです。",
    "model_id": "claude-opus-4"
  }'
```

### スキルとMCPサーバー

スキルとMCPサーバーはテナントに紐づき、`status`が`active`のものが自動的にエージェント実行時に使用されます。

## ワークスペース機能（S3）

ワークスペースは、会話ごとに独立したファイル空間を提供します。ファイルはAmazon S3に保存されます。

### 事前準備

1. S3バケットを作成（パブリックアクセスはブロック）
2. IAMポリシーでS3へのアクセス権限を付与
3. 環境変数を設定

```bash
S3_BUCKET_NAME=your-app-workspaces
S3_WORKSPACE_PREFIX=workspaces/
```

### ワークスペースの有効化

会話作成時に `enable_workspace: true` を指定：

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "enable_workspace": true
  }'
```

### ファイルアップロード付きエージェント実行

ストリーミングAPIでファイルを添付できます（multipart/form-data）：

```bash
curl -X POST "http://localhost:8000/api/tenants/tenant-001/conversations/uuid/stream" \
  -F 'request_data={
    "user_input": "このファイルを分析してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }' \
  -F "files=@data.csv"
```

### ファイル一覧の取得

```bash
curl "http://localhost:8000/api/tenants/tenant-001/conversations/uuid/files"
```

### ファイルのダウンロード

```bash
curl -O "http://localhost:8000/api/tenants/tenant-001/conversations/uuid/files/download?path=outputs/result.json"
```

詳細は [workspace.md](./workspace.md) を参照してください。

## MCPサーバー

Model Context Protocol (MCP) サーバーを使用して、外部サービスと連携できます。

### MCPサーバーの登録

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/mcp-servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "postgres-mcp",
    "display_name": "PostgreSQL MCP",
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-postgres"],
    "env": {
      "DATABASE_URL": "postgres://user:pass@host:5432/db"
    },
    "allowed_tools": ["query", "list_tables", "describe_table"]
  }'
```

### トークンの動的渡し

MCPサーバーが認証トークンを必要とする場合、実行時に渡すことができます。

```json
{
  "user_input": "...",
  "executor": {...},
  "tokens": {
    "servicenowToken": "dynamic-token-value"
  }
}
```

## スキル機能

スキルは、再利用可能なカスタム機能です。

### スキルの登録

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/skills \
  -H "Content-Type: application/json" \
  -d '{
    "name": "git-commit",
    "display_title": "Git Commit",
    "description": "変更をコミットする",
    "file_path": "/skills/tenant-001/git-commit"
  }'
```

### スキルファイルの配置

スキルファイルは以下の場所に配置します：

```
/skills/tenant_{tenant_id}/skills/{skill_name}/
├── CLAUDE.md      # スキルの説明
└── ...            # その他のファイル
```

### 優先スキルの指定

実行時に優先的に使用するスキルを指定できます：

```json
{
  "user_input": "変更をコミットしてください",
  "executor": {...},
  "preferred_skills": ["git-commit"]
}
```

## 使用量管理

### 使用量サマリーの取得

```bash
curl "http://localhost:8000/api/tenants/tenant-001/usage?from_date=2024-01-01&to_date=2024-01-31&group_by=day"
```

### レスポンス例

```json
[
  {
    "period": "2024-01-01T00:00:00",
    "total_tokens": 100000,
    "input_tokens": 60000,
    "output_tokens": 40000,
    "cache_creation_tokens": 5000,
    "cache_read_tokens": 10000,
    "total_cost_usd": 2.50,
    "execution_count": 50
  }
]
```

## ベストプラクティス

### 1. 会話管理

- **新しいタスクには新しい会話**: 関連のないタスクには新しい会話を使用
- **会話の継続**: 関連する質問は同じ会話で継続
- **会話のアーカイブ**: 不要になった会話はアーカイブ

### 2. テナント設定

- **適切なシステムプロンプト**: タスクに応じた明確な指示
- **デフォルトモデルの設定**: よく使用するモデルをデフォルトに
- **ワークスペースの活用**: ファイル操作が必要な場合はワークスペースを有効化

### 3. コスト管理

- **適切なモデル選択**: タスクの複雑さに応じたモデル選択
- **使用量モニタリング**: 定期的な使用量チェック
- **キャッシュの活用**: 同じコンテキストの再利用でコスト削減

### 4. エラーハンドリング

- **SSEの再接続**: 接続が切れた場合の再接続ロジック
- **タイムアウト処理**: 長時間実行タスクのタイムアウト設定
- **エラーログ**: エラー発生時のログ保存

### 5. セキュリティ

- **テナント分離**: テナントIDの適切な管理
- **トークン管理**: MCPサーバーのトークンは安全に管理
- **入力検証**: ユーザー入力の適切な検証

## トラブルシューティング

### よくある問題

#### SSE接続が切れる

**原因**: プロキシやロードバランサーのタイムアウト

**解決策**: タイムアウト設定を延長するか、キープアライブを設定

#### ツールが実行されない

**原因**: スキルやMCPサーバーのステータスが`inactive`

**解決策**: ステータスを`active`に更新

#### ワークスペースにアクセスできない

**原因**: 会話IDまたはテナントIDが不正、またはS3権限が不足

**解決策**: IDを確認し、IAMポリシーを確認

### ログの確認

```bash
docker logs claude-multi-agent
```

### デバッグモード

```bash
LOG_LEVEL=DEBUG docker-compose up
```
