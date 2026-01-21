# テナント管理API

テナント（組織/企業）の管理を行うAPIです。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/tenants` |
| 認証 | 必要 |
| スコープ | グローバル |

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants` | テナント一覧取得 |
| POST | `/api/tenants` | テナント作成 |
| GET | `/api/tenants/{tenant_id}` | テナント詳細取得 |
| PUT | `/api/tenants/{tenant_id}` | テナント更新 |
| DELETE | `/api/tenants/{tenant_id}` | テナント削除 |

---

## データ型

### TenantResponse

```typescript
interface TenantResponse {
  tenant_id: string;          // テナントID（一意識別子）
  system_prompt: string | null;  // システムプロンプト
  model_id: string | null;       // デフォルトモデルID
  status: "active" | "inactive"; // ステータス
  created_at: string;         // 作成日時（ISO 8601）
  updated_at: string;         // 更新日時（ISO 8601）
}
```

---

## GET /api/tenants

テナント一覧を取得します。

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `status` | string | No | - | ステータスフィルター (`active` / `inactive`) |
| `limit` | integer | No | 100 | 取得件数（1-1000） |
| `offset` | integer | No | 0 | オフセット |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "tenant_id": "acme-corp",
    "system_prompt": "あなたはACME社のアシスタントです。",
    "model_id": "claude-sonnet-4",
    "status": "active",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  },
  {
    "tenant_id": "tech-startup",
    "system_prompt": null,
    "model_id": null,
    "status": "active",
    "created_at": "2024-01-16T14:00:00Z",
    "updated_at": "2024-01-16T14:00:00Z"
  }
]
```

### curlの例

```bash
# 全テナント取得
curl -X GET "https://api.example.com/api/tenants" \
  -H "X-API-Key: your_api_key"

# アクティブなテナントのみ取得
curl -X GET "https://api.example.com/api/tenants?status=active&limit=50" \
  -H "X-API-Key: your_api_key"
```

---

## POST /api/tenants

新しいテナントを作成します。

### リクエストボディ

```typescript
interface TenantCreateRequest {
  tenant_id: string;           // テナントID（必須、一意）
  system_prompt?: string;      // システムプロンプト（オプション）
  model_id?: string;           // デフォルトモデルID（オプション）
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID（一意識別子） |
| `system_prompt` | string | No | システムプロンプト |
| `model_id` | string | No | デフォルトモデルID（modelsに存在する必要あり） |

### レスポンス

**成功時 (201 Created)**

```json
{
  "tenant_id": "new-tenant",
  "system_prompt": "あなたは親切なアシスタントです。",
  "model_id": "claude-sonnet-4",
  "status": "active",
  "created_at": "2024-01-17T09:00:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

**エラー: 重複 (409 Conflict)**

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "テナント 'new-tenant' は既に存在します",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

**エラー: モデル不存在 (400 Bad Request)**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "モデル 'invalid-model' が見つかりません",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

### curlの例

```bash
curl -X POST "https://api.example.com/api/tenants" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "new-tenant",
    "system_prompt": "あなたは親切なアシスタントです。",
    "model_id": "claude-sonnet-4"
  }'
```

---

## GET /api/tenants/{tenant_id}

指定したテナントの詳細を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### レスポンス

**成功時 (200 OK)**

```json
{
  "tenant_id": "acme-corp",
  "system_prompt": "あなたはACME社のアシスタントです。",
  "model_id": "claude-sonnet-4",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**エラー: 存在しない (404 Not Found)**

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
curl -X GET "https://api.example.com/api/tenants/acme-corp" \
  -H "X-API-Key: your_api_key"
```

---

## PUT /api/tenants/{tenant_id}

テナントを更新します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### リクエストボディ

```typescript
interface TenantUpdateRequest {
  system_prompt?: string;              // システムプロンプト
  model_id?: string;                   // デフォルトモデルID
  status?: "active" | "inactive";      // ステータス
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `system_prompt` | string | No | システムプロンプト |
| `model_id` | string | No | デフォルトモデルID |
| `status` | string | No | ステータス (`active` / `inactive`) |

**注意**: 指定したフィールドのみ更新されます。

### レスポンス

**成功時 (200 OK)**

```json
{
  "tenant_id": "acme-corp",
  "system_prompt": "更新されたシステムプロンプト",
  "model_id": "claude-opus-4",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-17T11:00:00Z"
}
```

**エラー: 存在しない (404 Not Found)**

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
curl -X PUT "https://api.example.com/api/tenants/acme-corp" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "system_prompt": "更新されたシステムプロンプト",
    "status": "inactive"
  }'
```

---

## DELETE /api/tenants/{tenant_id}

テナントを削除します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### レスポンス

**成功時 (204 No Content)**

レスポンスボディなし

**エラー: 存在しない (404 Not Found)**

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
curl -X DELETE "https://api.example.com/api/tenants/old-tenant" \
  -H "X-API-Key: your_api_key"
```

---

## 関連API

- [モデル管理API](./02-models.md) - テナントのデフォルトモデル設定
- [会話管理API](./03-conversations.md) - テナントの会話管理
- [Skills管理API](./05-skills.md) - テナントのSkill設定
- [MCPサーバー管理API](./06-mcp-servers.md) - テナントのMCPサーバー設定
