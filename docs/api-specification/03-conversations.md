# 会話管理API

会話（Conversation）のCRUD操作とメッセージログの取得を行うAPIです。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/tenants/{tenant_id}/conversations` |
| 認証 | 必要 |
| スコープ | テナント単位 |

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/conversations` | 会話一覧取得 |
| POST | `/api/tenants/{tenant_id}/conversations` | 会話作成 |
| GET | `/api/tenants/{tenant_id}/conversations/{conversation_id}` | 会話詳細取得 |
| PUT | `/api/tenants/{tenant_id}/conversations/{conversation_id}` | 会話更新 |
| POST | `/api/tenants/{tenant_id}/conversations/{conversation_id}/archive` | 会話アーカイブ |
| DELETE | `/api/tenants/{tenant_id}/conversations/{conversation_id}` | 会話削除 |
| GET | `/api/tenants/{tenant_id}/conversations/{conversation_id}/messages` | メッセージ一覧取得 |
| POST | `/api/tenants/{tenant_id}/conversations/{conversation_id}/stream` | **ストリーミング実行** |

**注意**: ストリーミング実行（`/stream`）は [04-streaming.md](./04-streaming.md) で詳細に解説します。

---

## データ型

### ConversationResponse

```typescript
interface ConversationResponse {
  conversation_id: string;        // 会話ID（UUID）
  session_id: string | null;      // Claude Agent SDKのセッションID
  tenant_id: string;              // テナントID
  user_id: string;                // ユーザーID
  model_id: string;               // 使用モデルID
  title: string | null;           // 会話タイトル（AI自動生成）
  status: "active" | "archived";  // ステータス
  workspace_enabled: boolean;     // ワークスペース有効フラグ

  // コンテキスト使用状況
  total_input_tokens: number;     // 累積入力トークン数
  total_output_tokens: number;    // 累積出力トークン数
  estimated_context_tokens: number; // 推定コンテキストトークン数
  context_limit_reached: boolean; // コンテキスト制限到達フラグ

  created_at: string;             // 作成日時（ISO 8601）
  updated_at: string;             // 更新日時（ISO 8601）
}
```

### コンテキスト使用状況の説明

| フィールド | 説明 |
|-----------|------|
| `total_input_tokens` | この会話での累積入力トークン数 |
| `total_output_tokens` | この会話での累積出力トークン数 |
| `estimated_context_tokens` | 次回リクエスト時の推定コンテキストサイズ |
| `context_limit_reached` | `true`の場合、この会話は送信不可（新しいチャットが必要） |

**注意**: `context_limit_reached: true` の会話に対してストリーミング実行を行うと、`context_limit_exceeded`エラーが返されます。フロントエンドは事前にこのフラグをチェックし、ユーザーに新しいチャットを開始するよう促すUIを表示すべきです。

### MessageLogResponse

```typescript
interface MessageLogResponse {
  message_id: string;                     // メッセージID
  conversation_id: string;                // 会話ID
  message_seq: number;                    // メッセージシーケンス番号
  message_type: string;                   // メッセージタイプ
  message_subtype: string | null;         // メッセージサブタイプ
  content: Record<string, any> | null;    // メッセージ内容（JSON）
  timestamp: string;                      // タイムスタンプ（ISO 8601）
}
```

---

## GET /api/tenants/{tenant_id}/conversations

テナントの会話一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `user_id` | string | No | - | ユーザーIDフィルター |
| `status` | string | No | - | ステータスフィルター (`active` / `archived`) |
| `from_date` | datetime | No | - | 開始日時（ISO 8601） |
| `to_date` | datetime | No | - | 終了日時（ISO 8601） |
| `limit` | integer | No | 50 | 取得件数（1-100） |
| `offset` | integer | No | 0 | オフセット |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "session_id": "sess_abc123",
    "tenant_id": "acme-corp",
    "user_id": "user-001",
    "model_id": "claude-sonnet-4",
    "title": "データ分析についての質問",
    "status": "active",
    "workspace_enabled": true,
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T11:45:00Z"
  },
  {
    "conversation_id": "660f9500-f39c-52e5-b827-557766550111",
    "session_id": "sess_def456",
    "tenant_id": "acme-corp",
    "user_id": "user-001",
    "model_id": "claude-sonnet-4",
    "title": "レポート作成の依頼",
    "status": "archived",
    "workspace_enabled": false,
    "created_at": "2024-01-14T09:00:00Z",
    "updated_at": "2024-01-14T10:30:00Z"
  }
]
```

**エラー: テナント不存在 (404 Not Found)**

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "テナント 'unknown-tenant' が見つかりません",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

### curlの例

```bash
# テナントの全会話取得
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations" \
  -H "X-API-Key: your_api_key"

# 特定ユーザーのアクティブな会話のみ取得
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations?user_id=user-001&status=active&limit=20" \
  -H "X-API-Key: your_api_key"

# 日付範囲で絞り込み
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations?from_date=2024-01-01T00:00:00Z&to_date=2024-01-31T23:59:59Z" \
  -H "X-API-Key: your_api_key"
```

---

## POST /api/tenants/{tenant_id}/conversations

新しい会話を作成します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### リクエストボディ

```typescript
interface ConversationCreateRequest {
  user_id: string;             // ユーザーID（必須）
  model_id?: string;           // モデルID（省略時はテナントのデフォルト）
  workspace_enabled?: boolean; // ワークスペース有効フラグ（デフォルト: true）
}
```

| フィールド | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `user_id` | string | Yes | - | ユーザーID |
| `model_id` | string | No | テナントのデフォルト | 使用するモデルID |
| `workspace_enabled` | boolean | No | true | ワークスペースを有効にするか |

### ワークスペースについて

`workspace_enabled: true` の場合：
- 会話専用のS3ワークスペースが作成されます
- ユーザーはファイルをアップロードできます
- AIはファイルを読み書きできます
- [ワークスペースAPI](./07-workspace.md) でファイル管理が可能です

### レスポンス

**成功時 (201 Created)**

```json
{
  "conversation_id": "770e9600-g49d-63e6-c938-668877660222",
  "session_id": null,
  "tenant_id": "acme-corp",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "title": null,
  "status": "active",
  "workspace_enabled": true,
  "created_at": "2024-01-17T09:00:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

**エラー: テナント不存在 (404 Not Found)**

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "テナント 'unknown-tenant' が見つかりません",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

**エラー: モデル未指定 (400 Bad Request)**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "model_idが指定されていません。リクエストまたはテナントのデフォルトモデルを設定してください。",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

**エラー: モデル利用不可 (400 Bad Request)**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "モデル 'claude-old' は現在利用できません",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

### curlの例

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/conversations" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "model_id": "claude-sonnet-4",
    "workspace_enabled": true
  }'
```

---

## GET /api/tenants/{tenant_id}/conversations/{conversation_id}

指定した会話の詳細を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID（UUID） |

### レスポンス

**成功時 (200 OK)**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_id": "sess_abc123",
  "tenant_id": "acme-corp",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "title": "データ分析についての質問",
  "status": "active",
  "workspace_enabled": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T11:45:00Z"
}
```

**エラー: 存在しない (404 Not Found)**

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "会話 '550e8400-e29b-41d4-a716-446655440000' が見つかりません",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000" \
  -H "X-API-Key: your_api_key"
```

---

## PUT /api/tenants/{tenant_id}/conversations/{conversation_id}

会話を更新します（タイトル変更等）。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID（UUID） |

### リクエストボディ

```typescript
interface ConversationUpdateRequest {
  title?: string;                       // タイトル（最大500文字）
  status?: "active" | "archived";       // ステータス
}
```

| フィールド | 型 | 必須 | 制限 | 説明 |
|-----------|-----|------|------|------|
| `title` | string | No | 最大500文字 | 会話タイトル |
| `status` | string | No | - | ステータス |

### レスポンス

**成功時 (200 OK)**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_id": "sess_abc123",
  "tenant_id": "acme-corp",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "title": "新しいタイトル",
  "status": "active",
  "workspace_enabled": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

### curlの例

```bash
curl -X PUT "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "新しいタイトル"
  }'
```

---

## POST /api/tenants/{tenant_id}/conversations/{conversation_id}/archive

会話をアーカイブします。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID（UUID） |

### リクエストボディ

空のオブジェクト `{}` または省略可能

### レスポンス

**成功時 (200 OK)**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_id": "sess_abc123",
  "tenant_id": "acme-corp",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "title": "データ分析についての質問",
  "status": "archived",
  "workspace_enabled": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

### curlの例

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000/archive" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

## DELETE /api/tenants/{tenant_id}/conversations/{conversation_id}

会話を削除します。関連するメッセージログも削除されます。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID（UUID） |

### レスポンス

**成功時 (204 No Content)**

レスポンスボディなし

### curlの例

```bash
curl -X DELETE "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/conversations/{conversation_id}/messages

会話の完全なメッセージログを取得します。デバッグ・監査用の詳細データです。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID（UUID） |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "message_id": "msg-001",
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "message_seq": 1,
    "message_type": "user",
    "message_subtype": null,
    "content": {
      "text": "こんにちは、データ分析について教えてください。"
    },
    "timestamp": "2024-01-15T10:30:00Z"
  },
  {
    "message_id": "msg-002",
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "message_seq": 2,
    "message_type": "assistant",
    "message_subtype": null,
    "content": {
      "text": "はい、データ分析について説明します...",
      "tool_calls": []
    },
    "timestamp": "2024-01-15T10:30:15Z"
  },
  {
    "message_id": "msg-003",
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "message_seq": 3,
    "message_type": "tool_result",
    "message_subtype": "Read",
    "content": {
      "tool_use_id": "tu_123",
      "result": "ファイル内容..."
    },
    "timestamp": "2024-01-15T10:30:20Z"
  }
]
```

### メッセージタイプ

| タイプ | 説明 |
|--------|------|
| `user` | ユーザーからの入力 |
| `assistant` | AIからの応答 |
| `tool_result` | ツール実行結果 |
| `system` | システムメッセージ |

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000/messages" \
  -H "X-API-Key: your_api_key"
```

---

## 会話フロー図

```
┌─────────────────────────────────────────────────────────────────┐
│                       会話のライフサイクル                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. POST /conversations        会話作成                          │
│         ↓                      (status: active, title: null)    │
│                                                                 │
│  2. POST /conversations/{id}/stream   ストリーミング実行          │
│         ↓                             (title: AI自動生成)        │
│                                                                 │
│  3. POST /conversations/{id}/stream   継続会話                   │
│         ↓                             (session_id で再開)        │
│                                                                 │
│  4. POST /conversations/{id}/archive  アーカイブ                 │
│         ↓                             (status: archived)        │
│                                                                 │
│  5. DELETE /conversations/{id}        削除                       │
│                                       (完全削除)                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 次のステップ

ストリーミング実行（`POST /conversations/{conversation_id}/stream`）の詳細は、
[04-streaming.md](./04-streaming.md) を参照してください。

---

## 関連API

- [ストリーミングAPI](./04-streaming.md) - 会話のストリーミング実行
- [ワークスペースAPI](./07-workspace.md) - 会話のファイル管理
- [使用状況API](./08-usage.md) - 会話ごとの使用量
