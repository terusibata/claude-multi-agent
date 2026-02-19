# MCPサーバー管理API

MCP（Model Context Protocol）サーバーの管理を行うAPIです。
OpenAPI仕様を登録し、AIエージェントに外部APIツールへのアクセスを提供します。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/tenants/{tenant_id}/mcp-servers` |
| 認証 | 必要 |
| スコープ | テナント単位 |

### MCPサーバーとは

MCPサーバーは、OpenAPI仕様を登録することでAIエージェントに外部APIツールを提供する仕組みです。
登録されたOpenAPI仕様から各エンドポイントが自動的にMCPツールに変換されます。

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/mcp-servers` | MCPサーバー一覧取得 |
| GET | `/api/tenants/{tenant_id}/mcp-servers/{server_id}` | MCPサーバー詳細取得 |
| POST | `/api/tenants/{tenant_id}/mcp-servers` | MCPサーバー登録 |
| PUT | `/api/tenants/{tenant_id}/mcp-servers/{server_id}` | MCPサーバー更新 |
| DELETE | `/api/tenants/{tenant_id}/mcp-servers/{server_id}` | MCPサーバー削除 |

---

## データ型

### McpServerResponse

```typescript
interface McpServerResponse {
  mcp_server_id: string;                    // MCPサーバーID
  tenant_id: string;                        // テナントID
  name: string;                             // サーバー名（識別子）
  display_name: string | null;              // 表示名
  env: Record<string, string> | null;       // 環境変数
  headers_template: Record<string, string> | null;  // ヘッダーテンプレート
  allowed_tools: string[] | null;           // 許可するツール名リスト
  description: string | null;               // 説明
  openapi_spec: object;                     // OpenAPI仕様
  openapi_base_url: string | null;          // OpenAPIベースURL
  status: "active" | "inactive";            // ステータス
  created_at: string;                       // 作成日時
  updated_at: string;                       // 更新日時
}
```

---

## GET /api/tenants/{tenant_id}/mcp-servers

テナントのMCPサーバー一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `status` | string | No | - | ステータスフィルター (`active` / `inactive`) |
| `limit` | integer | No | 50 | 取得件数（1-100） |
| `offset` | integer | No | 0 | オフセット |

### レスポンス

**成功時 (200 OK)**

```json
{
  "items": [
    {
      "mcp_server_id": "mcp-001",
      "tenant_id": "acme-corp",
      "name": "petstore",
      "display_name": "Pet Store API",
      "env": null,
      "headers_template": {
        "Authorization": "Bearer ${petstoreToken}"
      },
      "allowed_tools": ["mcp__petstore__listPets", "mcp__petstore__createPet"],
      "description": "ペットストアAPIとの連携",
      "openapi_spec": {
        "openapi": "3.0.0",
        "info": { "title": "Petstore", "version": "1.0" },
        "paths": { ... }
      },
      "openapi_base_url": "https://petstore.example.com/api",
      "status": "active",
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/mcp-servers" \
  -H "X-API-Key: your_api_key"
```

---

## POST /api/tenants/{tenant_id}/mcp-servers

新しいMCPサーバーを登録します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### リクエストボディ

```typescript
interface McpServerCreate {
  name: string;                             // サーバー名（必須、最大200文字）
  display_name?: string;                    // 表示名（最大300文字）
  env?: Record<string, string>;             // 環境変数
  headers_template?: Record<string, string>; // ヘッダーテンプレート
  allowed_tools?: string[];                 // 許可するツール名リスト
  description?: string;                     // 説明
  openapi_spec: object;                     // OpenAPI仕様（必須）
  openapi_base_url?: string;                // OpenAPIベースURL（最大500文字）
}
```

### headers_templateについて

`headers_template`では、プレースホルダを使用して動的な値を挿入できます：

```json
{
  "Authorization": "Bearer ${servicenowToken}",
  "X-Custom-Header": "${customValue}"
}
```

プレースホルダの値は、ストリーミングリクエストの`tokens`パラメータで渡します：

```json
{
  "user_input": "チケットを検索して",
  "executor": { ... },
  "tokens": {
    "servicenowToken": "actual-token-value",
    "customValue": "custom-header-value"
  }
}
```

### レスポンス

**成功時 (201 Created)**

```json
{
  "mcp_server_id": "mcp-002",
  "tenant_id": "acme-corp",
  "name": "petstore",
  "display_name": "Pet Store API",
  "openapi_spec": { ... },
  "openapi_base_url": "https://petstore.example.com/api",
  "status": "active",
  "created_at": "2024-01-17T09:00:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

### curlの例

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/mcp-servers" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "petstore",
    "display_name": "Pet Store API",
    "openapi_spec": {
      "openapi": "3.0.0",
      "info": { "title": "Petstore", "version": "1.0" },
      "paths": {
        "/pets": {
          "get": {
            "operationId": "listPets",
            "summary": "List all pets"
          }
        }
      }
    },
    "openapi_base_url": "https://petstore.example.com/api",
    "headers_template": {
      "Authorization": "Bearer ${petstoreToken}"
    }
  }'
```

---

## PUT /api/tenants/{tenant_id}/mcp-servers/{server_id}

MCPサーバー設定を更新します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `server_id` | string | Yes | MCPサーバーID |

### リクエストボディ

```typescript
interface McpServerUpdate {
  display_name?: string;
  env?: Record<string, string>;
  headers_template?: Record<string, string>;
  allowed_tools?: string[];
  description?: string;
  openapi_spec?: object;
  openapi_base_url?: string;
  status?: "active" | "inactive";
}
```

### レスポンス

**成功時 (200 OK)**

更新後のMCPサーバー情報

### curlの例

```bash
curl -X PUT "https://api.example.com/api/tenants/acme-corp/mcp-servers/mcp-001" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Updated Pet Store API",
    "status": "inactive"
  }'
```

---

## DELETE /api/tenants/{tenant_id}/mcp-servers/{server_id}

MCPサーバーを削除します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `server_id` | string | Yes | MCPサーバーID |

### レスポンス

**成功時 (204 No Content)**

レスポンスボディなし

### curlの例

```bash
curl -X DELETE "https://api.example.com/api/tenants/acme-corp/mcp-servers/mcp-001" \
  -H "X-API-Key: your_api_key"
```

---

## ツール名の形式

MCPサーバー経由で提供されるツールは、以下の形式で命名されます：

```
mcp__{server_name}__{tool_name}
```

例：
- `mcp__petstore__listPets`
- `mcp__servicenow__search_incidents`

ストリーミングの`init`イベントの`tools`配列で確認できます。

---

## 関連API

- [ストリーミングAPI](./04-streaming.md) - `tokens`でMCPサーバー認証情報を渡す
- [テナント管理API](./01-tenants.md) - テナント設定
