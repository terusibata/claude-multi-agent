# 使い方ガイド

Claude Multi-Agent API の使い方ガイドです。

## 目次

- [はじめに](#はじめに)
- [セットアップ](#セットアップ)
- [基本的な使い方](#基本的な使い方)
- [エージェント設定](#エージェント設定)
- [ワークスペース機能](#ワークスペース機能)
- [MCPサーバー](#mcpサーバー)
- [スキル機能](#スキル機能)
- [使用量管理](#使用量管理)
- [ベストプラクティス](#ベストプラクティス)

## はじめに

Claude Multi-Agent は、Claude Agent SDK を使用したマルチテナント対応のエージェント実行APIです。

### 主な機能

- **マルチテナント**: テナントごとに設定・使用量を分離
- **ストリーミング応答**: SSE形式でリアルタイム応答
- **ワークスペース**: 会話ごとの独立したファイル空間
- **MCP連携**: Model Context Protocolサーバーとの連携
- **スキル機能**: カスタム機能の追加

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
    "input_price_per_1m": "3.00",
    "output_price_per_1m": "15.00"
  }'
```

### 2. エージェント設定の作成

次に、エージェント設定を作成します。

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/agent-configs \
  -H "Content-Type: application/json" \
  -d '{
    "agent_config_id": "default-agent",
    "name": "デフォルトエージェント",
    "description": "基本的なエージェント設定",
    "system_prompt": "あなたは親切なアシスタントです。",
    "allowed_tools": ["Read", "Write", "Bash", "Glob", "Grep"],
    "permission_mode": "default"
  }'
```

### 3. エージェントの実行

エージェントを実行します（SSEストリーミング）。

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/execute \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "agent_config_id": "default-agent",
    "model_id": "claude-sonnet-4",
    "user_input": "Pythonでソートアルゴリズムを実装してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }'
```

### 4. 会話の継続

同じ会話で続ける場合は、`conversation_id`を指定します。

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/execute \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "agent_config_id": "default-agent",
    "model_id": "claude-sonnet-4",
    "conversation_id": "conversation-uuid-from-previous-response",
    "user_input": "バブルソートも追加してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }'
```

### 5. フロントエンド推奨フロー

フロントエンドアプリケーションでは、以下のフローを推奨します：

```
1. POST /conversations → 会話を作成（conversation_idを取得）
2. ページ遷移（/chat/{conversation_id}など）
3. POST /conversations/{conversation_id}/stream → ストリーミング実行
```

**Step 1: 会話を作成**

```bash
curl -X POST "http://localhost:8000/api/tenants/tenant-001/conversations" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "agent_config_id": "default-agent"
  }'
```

レスポンス：
```json
{
  "conversation_id": "abc123-uuid",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "agent_config_id": "default-agent",
  "status": "active",
  "created_at": "2024-01-01T00:00:00Z"
}
```

タイトルはストリーミング実行時にAIが自動生成します。

**Step 2: ストリーミング実行**

```bash
curl -X POST "http://localhost:8000/api/tenants/tenant-001/conversations/abc123-uuid/stream" \
  -F 'request_data={
    "agent_config_id": "default-agent",
    "model_id": "claude-sonnet-4",
    "user_input": "Pythonでソートアルゴリズムを実装してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }'
```

## エージェント設定

### 利用可能なツール

```json
{
  "allowed_tools": [
    "Read",      // ファイル読み取り
    "Write",     // ファイル書き込み
    "Edit",      // ファイル編集
    "Bash",      // シェルコマンド実行
    "Glob",      // ファイル検索（パターン）
    "Grep",      // ファイル内検索
    "Skill"      // スキル実行
  ]
}
```

### パーミッションモード

| モード | 説明 |
|--------|------|
| `default` | 通常のパーミッション |
| `bypassPermissions` | パーミッションをバイパス（危険な操作も許可） |

### システムプロンプト

エージェントの振る舞いをカスタマイズするシステムプロンプトを設定できます。

```json
{
  "system_prompt": "あなたはソフトウェアエンジニアです。コードレビューを行い、改善点を提案してください。"
}
```

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

エージェント設定で有効化：

```json
{
  "workspace_enabled": true
}
```

### ファイルアップロード付きエージェント実行

`/execute` APIでファイルを添付できます（multipart/form-data）：

```bash
curl -X POST "http://localhost:8000/api/tenants/tenant-001/execute" \
  -F 'request_data={
    "agent_config_id": "default-agent",
    "model_id": "claude-sonnet-4",
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
curl "http://localhost:8000/api/tenants/tenant-001/conversations/conversation-001/files"
```

### ファイルのダウンロード

```bash
curl -O "http://localhost:8000/api/tenants/tenant-001/conversations/conversation-001/files/download?path=outputs/result.json"
```

### Presentedファイル

AIが生成してユーザーに提示したいファイルは自動的に「Presented」としてマークされます（`outputs/`ディレクトリ以下）。

```bash
# Presentedファイル一覧の取得
curl "http://localhost:8000/api/tenants/tenant-001/conversations/conversation-001/files/presented"
```

### セキュリティ

- **S3アクセス制御**: バケットは完全プライベート、APIサーバー経由でのみアクセス
- **テナント・会話分離**: 異なるテナント・会話間でのアクセスを禁止

詳細は [workspace.md](./workspace.md) を参照してください。

## MCPサーバー

Model Context Protocol (MCP) サーバーを使用して、外部サービスと連携できます。

### MCPサーバーの登録

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/mcp-servers \
  -H "Content-Type: application/json" \
  -d '{
    "server_id": "postgres-mcp",
    "name": "PostgreSQL MCP",
    "description": "PostgreSQLデータベース接続",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-postgres"],
    "env": {
      "DATABASE_URL": "postgres://user:pass@host:5432/db"
    },
    "allowed_tools": ["query", "list_tables", "describe_table"]
  }'
```

### エージェント設定へのMCPサーバー追加

```json
{
  "mcp_servers": ["postgres-mcp", "github-mcp"]
}
```

### トークンの動的渡し

MCPサーバーが認証トークンを必要とする場合、実行時に渡すことができます。

```json
{
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
    "skill_id": "git-commit",
    "name": "Git Commit",
    "description": "変更をコミットする",
    "skill_path": "skills/git-commit"
  }'
```

### スキルファイルの配置

スキルファイルは以下の場所に配置します：

```
/skills/tenant_{tenant_id}/skills/{skill_id}/
├── CLAUDE.md      # スキルの説明
└── ...            # その他のファイル
```

### エージェント設定へのスキル追加

```json
{
  "agent_skills": ["git-commit", "code-review"]
}
```

## 使用量管理

### 使用量サマリーの取得

```bash
curl "http://localhost:8000/api/tenants/tenant-001/usage?start_date=2024-01-01&end_date=2024-01-31"
```

### レスポンス例

```json
{
  "tenant_id": "tenant-001",
  "period": {
    "start_date": "2024-01-01",
    "end_date": "2024-01-31"
  },
  "summary": {
    "total_input_tokens": 1000000,
    "total_output_tokens": 500000,
    "total_cost_usd": 25.50
  },
  "by_model": [
    {
      "model_id": "claude-sonnet-4",
      "input_tokens": 800000,
      "cost_usd": 18.00
    }
  ],
  "by_user": [
    {
      "user_id": "user-001",
      "input_tokens": 500000,
      "cost_usd": 12.75
    }
  ]
}
```

## ベストプラクティス

### 1. 会話管理

- **新しいタスクには新しい会話**: 関連のないタスクには新しい会話を使用
- **会話の継続**: 関連する質問は同じ会話で継続
- **会話のアーカイブ**: 不要になった会話はアーカイブ

### 2. エージェント設定

- **最小権限の原則**: 必要なツールのみを許可
- **適切なシステムプロンプト**: タスクに応じた明確な指示
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

**原因**: `allowed_tools` に含まれていない

**解決策**: エージェント設定で必要なツールを許可

#### ワークスペースにアクセスできない

**原因**: 会話IDまたはテナントIDが不正

**解決策**: 正しいIDを使用しているか確認

### ログの確認

```bash
docker logs claude-multi-agent
```

### デバッグモード

```bash
LOG_LEVEL=DEBUG docker-compose up
```
