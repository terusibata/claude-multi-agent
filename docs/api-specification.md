# API仕様書

Claude Multi-Agent API のエンドポイント仕様書です。

## 目次

- [概要](#概要)
- [認証](#認証)
- [共通仕様](#共通仕様)
- [エンドポイント一覧](#エンドポイント一覧)
  - [Models](#models)
  - [Agent Configs](#agent-configs)
  - [Sessions](#sessions)
  - [Execute](#execute)
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
  "detail": "エラーメッセージ"
}
```

または詳細なエラー形式：

```json
{
  "detail": {
    "message": "エラーメッセージ",
    "error_code": "ERROR_CODE",
    "details": {}
  }
}
```

## 認証

現在、認証は実装されていません。テナントIDをパスパラメータとして使用します。

## 共通仕様

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
    "input_price_per_1m": "3.00",
    "output_price_per_1m": "15.00",
    "cache_write_price_per_1m": "3.75",
    "cache_read_price_per_1m": "0.30",
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
  "input_price_per_1m": "15.00",
  "output_price_per_1m": "75.00"
}
```

#### PUT /api/models/{model_id}

モデル情報を更新

#### PATCH /api/models/{model_id}/status

モデルのステータスを更新

---

### Agent Configs

エージェント実行設定の管理API。

#### GET /api/tenants/{tenant_id}/agent-configs

エージェント設定一覧を取得

**レスポンス:**

```json
[
  {
    "agent_config_id": "default-agent",
    "tenant_id": "tenant-001",
    "name": "デフォルトエージェント",
    "description": "基本的なエージェント設定",
    "system_prompt": "あなたは親切なアシスタントです。",
    "allowed_tools": ["Read", "Write", "Bash", "Glob", "Grep"],
    "permission_mode": "default",
    "mcp_servers": [],
    "agent_skills": [],
    "workspace_enabled": false,
    "status": "active",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

#### POST /api/tenants/{tenant_id}/agent-configs

新しいエージェント設定を作成

**リクエスト:**

```json
{
  "agent_config_id": "coding-agent",
  "name": "コーディングエージェント",
  "description": "コーディング支援用のエージェント",
  "system_prompt": "あなたは優秀なソフトウェアエンジニアです。",
  "allowed_tools": ["Read", "Write", "Bash", "Glob", "Grep", "Edit"],
  "permission_mode": "default",
  "mcp_servers": [],
  "agent_skills": [],
  "workspace_enabled": true
}
```

#### PUT /api/tenants/{tenant_id}/agent-configs/{agent_config_id}

エージェント設定を更新

#### DELETE /api/tenants/{tenant_id}/agent-configs/{agent_config_id}

エージェント設定を削除（論理削除）

---

### Sessions

セッション（会話履歴）の管理API。

#### GET /api/tenants/{tenant_id}/sessions

セッション一覧を取得

**クエリパラメータ:**

- `status`: ステータスでフィルタ（active/archived）
- `limit`: 取得件数
- `offset`: オフセット

**レスポンス:**

```json
[
  {
    "chat_session_id": "session-uuid-001",
    "tenant_id": "tenant-001",
    "user_id": "user-001",
    "agent_config_id": "default-agent",
    "session_id": "sdk-session-id",
    "title": "プログラミングについての質問",
    "status": "active",
    "workspace_enabled": false,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

#### GET /api/tenants/{tenant_id}/sessions/{session_id}

特定のセッションを取得

#### GET /api/tenants/{tenant_id}/sessions/{session_id}/messages

セッションのメッセージ履歴を取得

**レスポンス:**

```json
{
  "chat_session_id": "session-uuid-001",
  "messages": [
    {
      "message_seq": 1,
      "message_type": "user",
      "message_subtype": null,
      "content": {
        "type": "user",
        "text": "Pythonでソートアルゴリズムを教えてください"
      },
      "created_at": "2024-01-01T00:00:00Z"
    },
    {
      "message_seq": 2,
      "message_type": "system",
      "message_subtype": "init",
      "content": {
        "type": "system",
        "subtype": "init",
        "data": {
          "session_id": "sdk-session-id",
          "tools": ["Read", "Write"],
          "model": "Claude Sonnet 4"
        }
      },
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

#### GET /api/tenants/{tenant_id}/sessions/{session_id}/display

UI表示用のメッセージ履歴を取得（整形済み）

#### PUT /api/tenants/{tenant_id}/sessions/{session_id}

セッション情報を更新

---

### Execute

エージェント実行API（SSEストリーミング）。

#### POST /api/tenants/{tenant_id}/execute

エージェントを実行（SSEストリーミング、ファイル添付対応）

**Content-Type:** `multipart/form-data`

**リクエストパラメータ:**

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `request_data` | string | ○ | ExecuteRequestのJSON文字列 |
| `files` | File[] | - | 添付ファイル（複数可、オプション） |

**ExecuteRequest JSON フィールド:**

```json
{
  "agent_config_id": "default-agent",
  "model_id": "claude-sonnet-4",
  "chat_session_id": "session-uuid-001",
  "user_input": "Pythonでソートアルゴリズムを教えてください",
  "executor": {
    "user_id": "user-001",
    "name": "田中太郎",
    "email": "tanaka@example.com"
  },
  "tokens": {},
  "resume_session_id": null,
  "fork_session": false,
  "enable_workspace": false
}
```

**cURLの例（ファイル添付あり）:**

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
  -F "files=@/path/to/document.pdf"
```

**レスポンス:** Server-Sent Events (SSE)

詳細は [streaming-specification.md](./streaming-specification.md) を参照してください。

---

### Workspace

セッション専用ワークスペースの管理API（S3ベース）。

ファイルはAmazon S3に保存され、APIサーバー経由でのみアクセス可能です。

#### GET /api/tenants/{tenant_id}/sessions/{session_id}/files

ファイル一覧を取得

**レスポンス:**

```json
{
  "chat_session_id": "session-uuid-001",
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

#### GET /api/tenants/{tenant_id}/sessions/{session_id}/files/download

ファイルをダウンロード

**クエリパラメータ:**

- `path`: ファイルパス（必須）

**レスポンス:**

- `Content-Type`: ファイルのMIMEタイプ
- `Content-Disposition`: `attachment; filename="ファイル名"`
- Body: ファイルのバイナリデータ

#### GET /api/tenants/{tenant_id}/sessions/{session_id}/files/presented

AIが提示したファイル一覧を取得

**レスポンス:**

```json
{
  "chat_session_id": "session-uuid-001",
  "files": [
    {
      "file_id": "file-uuid-002",
      "file_path": "outputs/result.json",
      "original_name": "result.json",
      "file_size": 512,
      "source": "ai_created",
      "is_presented": true
    }
  ]
}
```

---

### Skills

スキル（カスタム機能）の管理API。

#### GET /api/tenants/{tenant_id}/skills

スキル一覧を取得

#### POST /api/tenants/{tenant_id}/skills

新しいスキルを登録

**リクエスト:**

```json
{
  "skill_id": "git-commit",
  "name": "Git Commit",
  "description": "コードの変更をコミット",
  "skill_path": "skills/git-commit"
}
```

#### PUT /api/tenants/{tenant_id}/skills/{skill_id}

スキルを更新

#### DELETE /api/tenants/{tenant_id}/skills/{skill_id}

スキルを削除

---

### MCP Servers

MCPサーバーの管理API。

#### GET /api/tenants/{tenant_id}/mcp-servers

MCPサーバー一覧を取得

#### POST /api/tenants/{tenant_id}/mcp-servers

新しいMCPサーバーを登録

**リクエスト:**

```json
{
  "server_id": "postgres-mcp",
  "name": "PostgreSQL MCP",
  "description": "PostgreSQLデータベース接続",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-postgres"],
  "env": {
    "DATABASE_URL": "postgres://..."
  },
  "allowed_tools": ["query", "list_tables"]
}
```

#### PUT /api/tenants/{tenant_id}/mcp-servers/{server_id}

MCPサーバーを更新

#### DELETE /api/tenants/{tenant_id}/mcp-servers/{server_id}

MCPサーバーを削除

---

### Usage

使用量・コスト情報の取得API。

#### GET /api/tenants/{tenant_id}/usage

使用量サマリーを取得

**クエリパラメータ:**

- `start_date`: 開始日（YYYY-MM-DD）
- `end_date`: 終了日（YYYY-MM-DD）
- `group_by`: グループ化（day/week/month）

**レスポンス:**

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
    "total_cache_creation_tokens": 100000,
    "total_cache_read_tokens": 200000,
    "total_cost_usd": 25.50
  },
  "by_model": [
    {
      "model_id": "claude-sonnet-4",
      "input_tokens": 800000,
      "output_tokens": 400000,
      "cost_usd": 18.00
    }
  ],
  "by_user": [
    {
      "user_id": "user-001",
      "input_tokens": 500000,
      "output_tokens": 250000,
      "cost_usd": 12.75
    }
  ]
}
```

#### GET /api/tenants/{tenant_id}/usage/logs

使用量ログ詳細を取得

---

## HTTPステータスコード

| コード | 説明 |
|--------|------|
| 200 | 成功 |
| 201 | 作成成功 |
| 400 | リクエストエラー |
| 403 | アクセス拒否 |
| 404 | リソースが見つからない |
| 413 | ファイルサイズ超過 |
| 500 | サーバーエラー |

---

## 制限事項

### ワークスペース（S3）

ファイルはAmazon S3に保存されます。S3ライフサイクルポリシーでの自動削除・移行を推奨します。

### リクエスト

- リクエストタイムアウト: 300秒（実行エンドポイント）
- その他のエンドポイント: 30秒
