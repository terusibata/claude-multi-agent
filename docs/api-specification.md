# API仕様書

Claude Multi-Agent API のエンドポイント仕様書です。

## 目次

- [概要](#概要)
- [共通仕様](#共通仕様)
- [エンドポイント一覧](#エンドポイント一覧)
  - [Tenants](#tenants)
  - [Models](#models)
  - [Conversations](#conversations)
  - [Simple Chats](#simple-chats)
  - [Workspace](#workspace)
  - [Skills](#skills)
  - [MCP Servers](#mcp-servers)
  - [Usage](#usage)

## 概要

### ベースURL

```
http://localhost:8000/api
```

### レスポンス形式

すべてのレスポンスはJSON形式です。

### エラーレスポンス

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "エラーメッセージ",
    "details": [...],
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "timestamp": "2024-01-15T10:30:00Z"
  }
}
```

## 共通仕様

### 認証

APIへのアクセスには認証が必要です。以下のいずれかの方法でAPIキーを送信してください：

```bash
# X-API-Key ヘッダー（推奨）
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/tenants

# Authorization ヘッダー
curl -H "Authorization: Bearer your-api-key" http://localhost:8000/api/tenants
```

認証が不要なエンドポイント: `/`, `/health`, `/health/live`, `/health/ready`

詳細は [security.md](./security.md) を参照してください。

### レート制限

ユーザー単位でのレート制限が適用されます。

**必要なヘッダー:**

| ヘッダー | 説明 |
|----------|------|
| `X-Tenant-ID` | テナントID |
| `X-User-ID` | ユーザーID |

**レスポンスヘッダー:**

| ヘッダー | 説明 |
|----------|------|
| `X-RateLimit-Limit` | ウィンドウあたりの上限 |
| `X-RateLimit-Remaining` | 残りリクエスト数 |
| `X-RateLimit-Reset` | リセット時刻（Unix timestamp） |

制限超過時は `429 Too Many Requests` が返されます。

### リクエストトレーシング

すべてのリクエストに `X-Request-ID` が付与されます。障害調査時に使用してください。

```bash
# リクエスト時にIDを指定可能
curl -H "X-Request-ID: my-trace-id" http://localhost:8000/api/tenants

# レスポンスヘッダーで確認
# X-Request-ID: my-trace-id
# X-Process-Time: 0.0234
```

### テナントID

ほとんどのエンドポイントは `tenant_id` をパスパラメータとして必要とします。

```
/api/tenants/{tenant_id}/...
```

### ページネーション

リスト取得系のエンドポイントでは、以下のクエリパラメータをサポートします：

- `limit`: 取得件数（デフォルト: 100）
- `offset`: オフセット（デフォルト: 0）

---

## エンドポイント一覧

### Tenants

テナント管理API。テナントはマルチテナント環境における組織単位です。

#### GET /api/tenants

テナント一覧を取得

**クエリパラメータ:**

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `status` | string | フィルター（active/inactive） |
| `limit` | int | 取得件数 |
| `offset` | int | オフセット |

**レスポンス:**

```json
[
  {
    "tenant_id": "tenant-001",
    "system_prompt": "あなたは親切なアシスタントです。",
    "model_id": "claude-sonnet-4",
    "status": "active",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

#### POST /api/tenants

テナントを作成

**リクエスト:**

```json
{
  "tenant_id": "tenant-001",
  "system_prompt": "あなたは親切なアシスタントです。",
  "model_id": "claude-sonnet-4"
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | ○ | テナントID（一意） |
| `system_prompt` | string | - | システムプロンプト |
| `model_id` | string | - | デフォルトモデルID |

#### GET /api/tenants/{tenant_id}

テナントを取得

#### PUT /api/tenants/{tenant_id}

テナントを更新

**リクエスト:**

```json
{
  "system_prompt": "更新されたシステムプロンプト",
  "model_id": "claude-opus-4",
  "status": "active"
}
```

#### DELETE /api/tenants/{tenant_id}

テナントを削除

---

### Models

モデル定義の管理API。

#### GET /api/models

利用可能なモデル一覧を取得

**レスポンス:**

```json
[
  {
    "model_id": "claude-sonnet-4",
    "display_name": "Claude Sonnet 4",
    "bedrock_model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "model_region": "us-west-2",
    "input_token_price": "3.000000",
    "output_token_price": "15.000000",
    "cache_creation_price": "3.750000",
    "cache_read_price": "0.300000",
    "status": "active",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

#### GET /api/models/{model_id}

特定のモデルを取得

#### POST /api/models

新しいモデルを登録

**リクエスト:**

```json
{
  "model_id": "claude-opus-4",
  "display_name": "Claude Opus 4",
  "bedrock_model_id": "us.anthropic.claude-opus-4-20250514-v1:0",
  "model_region": "us-west-2",
  "input_token_price": "15.00",
  "output_token_price": "75.00"
}
```

#### PUT /api/models/{model_id}

モデル情報を更新

#### PATCH /api/models/{model_id}/status

モデルのステータスを更新

#### DELETE /api/models/{model_id}

モデルを削除（紐づきがない場合のみ）

**制約:**
- テナントのデフォルトモデルとして使用されていないこと
- 会話で使用されていないこと
- 使用量ログに記録がないこと

**エラーレスポンス（409 Conflict）:**

```json
{
  "detail": {
    "message": "モデル 'claude-sonnet-4' は使用中のため削除できません",
    "usage": {
      "tenants": 2,
      "conversations": 15,
      "usage_logs": 100
    }
  }
}
```

---

### Conversations

会話の管理・実行API。

#### GET /api/tenants/{tenant_id}/conversations

会話一覧を取得

**クエリパラメータ:**

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `status` | string | ステータスでフィルタ（active/archived） |
| `user_id` | string | ユーザーIDでフィルタ |
| `from_date` | string | 開始日時（ISO 8601形式、JSTデフォルト） |
| `to_date` | string | 終了日時（ISO 8601形式、JSTデフォルト） |
| `limit` | int | 取得件数 |
| `offset` | int | オフセット |

**レスポンス:**

```json
[
  {
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "tenant_id": "tenant-001",
    "user_id": "user-001",
    "model_id": "claude-sonnet-4",
    "session_id": "sdk-session-id",
    "title": "プログラミングについての質問",
    "status": "active",
    "workspace_enabled": false,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

#### POST /api/tenants/{tenant_id}/conversations

新しい会話を作成

**リクエスト:**

```json
{
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "workspace_enabled": false
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `user_id` | string | ○ | ユーザーID |
| `model_id` | string | - | モデルID（省略時はテナントのデフォルト） |
| `workspace_enabled` | boolean | - | ワークスペースを有効にするか（デフォルト: false） |

**レスポンス:**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "session_id": null,
  "title": null,
  "status": "active",
  "workspace_enabled": false,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

#### GET /api/tenants/{tenant_id}/conversations/{conversation_id}

会話を取得

#### GET /api/tenants/{tenant_id}/conversations/{conversation_id}/messages

会話のメッセージ履歴を取得

**レスポンス:**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [
    {
      "message_seq": 1,
      "message_type": "user",
      "message_subtype": null,
      "content": {
        "type": "user",
        "text": "Pythonでソートアルゴリズムを教えてください"
      },
      "timestamp": "2024-01-01T00:00:00Z"
    }
  ]
}
```

#### PUT /api/tenants/{tenant_id}/conversations/{conversation_id}

会話を更新（タイトル変更など）

#### DELETE /api/tenants/{tenant_id}/conversations/{conversation_id}

会話を削除

---

### POST /api/tenants/{tenant_id}/conversations/{conversation_id}/stream

会話でエージェント実行（SSEストリーミング、ファイル添付対応）

**Content-Type:** `multipart/form-data`

**リクエストパラメータ:**

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `request_data` | string | ○ | StreamRequestのJSON文字列 |
| `files` | File[] | - | 添付ファイル（複数可、オプション） |

**StreamRequest JSON フィールド:**

```json
{
  "user_input": "質問内容",
  "executor": {
    "user_id": "user-001",
    "name": "田中太郎",
    "email": "tanaka@example.com"
  },
  "tokens": {},
  "preferred_skills": ["skill-name"]
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `user_input` | string | ○ | ユーザー入力 |
| `executor` | object | ○ | 実行者情報 |
| `tokens` | object | - | MCPサーバー用認証トークン |
| `preferred_skills` | string[] | - | 優先的に使用するスキル名 |

**cURLの例:**

```bash
curl -X POST "http://localhost:8000/api/tenants/tenant-001/conversations/550e8400-uuid/stream" \
  -F 'request_data={
    "user_input": "このファイルを分析してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }' \
  -F "files=@/path/to/document.pdf"
```

**レスポンス:** Server-Sent Events (SSE)

詳細は [streaming-specification.md](./streaming-specification.md) を参照してください。

---

### Workspace

会話専用ワークスペースの管理API（S3ベース）。

#### GET /api/tenants/{tenant_id}/conversations/{conversation_id}/files

ファイル一覧を取得

**レスポンス:**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "files": [
    {
      "file_id": "file-uuid-001",
      "file_path": "uploads/data.csv",
      "original_name": "data.csv",
      "file_size": 1024,
      "mime_type": "text/csv",
      "version": 1,
      "source": "user_upload",
      "is_presented": false,
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z"
    }
  ],
  "total_count": 1,
  "total_size": 1024
}
```

#### GET /api/tenants/{tenant_id}/conversations/{conversation_id}/files/download

ファイルをダウンロード

**クエリパラメータ:**

- `path`: ファイルパス（必須）

**レスポンス:**

- `Content-Type`: ファイルのMIMEタイプ
- `Content-Disposition`: `attachment; filename="ファイル名"`
- Body: ファイルのバイナリデータ

#### GET /api/tenants/{tenant_id}/conversations/{conversation_id}/files/presented

AIが提示したファイル一覧を取得

---

### Skills

スキル（カスタム機能）の管理API。

#### GET /api/tenants/{tenant_id}/skills

スキル一覧を取得

**レスポンス:**

```json
[
  {
    "skill_id": "550e8400-e29b-41d4-a716-446655440000",
    "tenant_id": "tenant-001",
    "name": "git-commit",
    "display_title": "Git Commit",
    "description": "コードの変更をコミット",
    "version": 1,
    "file_path": "/skills/tenant-001/git-commit",
    "slash_command": "/commit",
    "slash_command_description": "変更をコミットします",
    "is_user_selectable": true,
    "status": "active",
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

#### POST /api/tenants/{tenant_id}/skills

新しいスキルを登録

#### PUT /api/tenants/{tenant_id}/skills/{skill_id}

スキルを更新

#### DELETE /api/tenants/{tenant_id}/skills/{skill_id}

スキルを削除（論理削除）

---

### MCP Servers

MCPサーバーの管理API。

#### GET /api/tenants/{tenant_id}/mcp-servers

MCPサーバー一覧を取得

**レスポンス:**

```json
[
  {
    "mcp_server_id": "550e8400-e29b-41d4-a716-446655440000",
    "tenant_id": "tenant-001",
    "name": "postgres-mcp",
    "display_name": "PostgreSQL MCP",
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-postgres"],
    "description": "PostgreSQLデータベース接続",
    "status": "active",
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

#### POST /api/tenants/{tenant_id}/mcp-servers

新しいMCPサーバーを登録

**リクエスト:**

```json
{
  "name": "postgres-mcp",
  "display_name": "PostgreSQL MCP",
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-postgres"],
  "env": {
    "DATABASE_URL": "postgres://..."
  },
  "allowed_tools": ["query", "list_tables"]
}
```

#### PUT /api/tenants/{tenant_id}/mcp-servers/{mcp_server_id}

MCPサーバーを更新

#### DELETE /api/tenants/{tenant_id}/mcp-servers/{mcp_server_id}

MCPサーバーを削除（論理削除）

---

### Simple Chats

シンプルチャットAPI。Claude Agent SDKを使わず、AWS Bedrock Converse APIを直接呼び出すシンプルなチャット機能を提供します。

**特徴:**

- SDKを使わない直接Bedrock API呼び出し
- テキストのみのチャット（添付ファイルなし）
- ストリーミング応答（SSE形式）
- 会話履歴の引き継ぎ
- タイトル自動生成（初回応答完了時）
- `application_type` による用途識別

詳細は [10-simple-chat.md](./api-specification/10-simple-chat.md) を参照してください。

#### POST /api/tenants/{tenant_id}/simple-chats/stream

ストリーミング実行（新規作成・継続を統合）

**動作モード:**
- `chat_id` を指定しない → **新規作成**
- `chat_id` を指定する → **継続**

**新規作成リクエスト:**

```json
{
  "user_id": "user-001",
  "application_type": "translationApp",
  "system_prompt": "You are a professional translator.",
  "model_id": "claude-sonnet-4",
  "message": "Hello, how are you?"
}
```

**継続リクエスト:**

```json
{
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Now translate it to French."
}
```

**レスポンス:** `Content-Type: text/event-stream`

新規作成時はレスポンスヘッダー `X-Chat-ID` にチャットIDが含まれます。

#### GET /api/tenants/{tenant_id}/simple-chats

シンプルチャット一覧を取得

#### GET /api/tenants/{tenant_id}/simple-chats/{chat_id}

シンプルチャット詳細（メッセージ履歴含む）を取得

#### POST /api/tenants/{tenant_id}/simple-chats/{chat_id}/archive

シンプルチャットをアーカイブ

#### DELETE /api/tenants/{tenant_id}/simple-chats/{chat_id}

シンプルチャットを削除

---

### Usage

使用量・コスト情報の取得API。

#### GET /api/tenants/{tenant_id}/usage

使用量サマリーを取得

**クエリパラメータ:**

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `from_date` | string | 開始日時（ISO 8601形式、JSTデフォルト） |
| `to_date` | string | 終了日時（ISO 8601形式、JSTデフォルト） |
| `group_by` | string | グループ化（day/week/month） |

**レスポンス:**

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

#### GET /api/tenants/{tenant_id}/cost-report

コストレポートを取得

**クエリパラメータ:**

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `from_date` | string | ○ | 開始日時（ISO 8601形式、JSTデフォルト） |
| `to_date` | string | ○ | 終了日時（ISO 8601形式、JSTデフォルト） |
| `model_id` | string | - | モデルIDでフィルタ |
| `user_id` | string | - | ユーザーIDでフィルタ |

---

## HTTPステータスコード

| コード | 説明 |
|--------|------|
| 200 | 成功 |
| 201 | 作成成功 |
| 204 | 削除成功 |
| 400 | リクエストエラー |
| 404 | リソースが見つからない |
| 409 | 競合（既に存在するなど） |
| 500 | サーバーエラー |

---

## 制限事項

### ストリーミング実行

- リクエストタイムアウト: 300秒
- クライアント切断後も処理は継続

### ワークスペース（S3）

- ファイルはAmazon S3に保存
- 会話ごとに独立したワークスペース
- テナント・会話間で完全に分離

### 日時の取り扱い

- 日時パラメータはISO 8601形式で指定
- タイムゾーン情報がない場合、**JST（日本標準時、UTC+9）** として扱う
- 内部的にはUTCに変換して処理
- レスポンスの日時はUTCで返却

**例:**
```
# JSTとして扱われる（タイムゾーン指定なし）
from_date=2024-01-01T00:00:00

# 明示的にJSTを指定
from_date=2024-01-01T00:00:00+09:00

# UTCを指定
from_date=2024-01-01T00:00:00Z
```
