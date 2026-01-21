# MCPサーバー管理API

MCP（Model Context Protocol）サーバーの管理を行うAPIです。
MCPサーバーを通じて、AIエージェントに外部ツールやサービスへのアクセスを提供します。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/tenants/{tenant_id}/mcp-servers` |
| 認証 | 必要 |
| スコープ | テナント単位 |

### MCPサーバーとは

MCPサーバーは、AIエージェントに追加のツールを提供する仕組みです。
以下の種類をサポートしています：

| タイプ | 説明 | 例 |
|--------|------|-----|
| `http` | HTTP APIベースのサーバー | REST API |
| `sse` | Server-Sent Events接続 | ストリーミングAPI |
| `stdio` | 標準入出力で通信 | CLIツール |
| `builtin` | 組み込みツール定義 | カスタムツール |
| `openapi` | OpenAPI仕様ベース | Swagger定義済みAPI |

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/mcp-servers` | MCPサーバー一覧取得 |
| GET | `/api/tenants/{tenant_id}/mcp-servers/builtin` | ビルトインサーバー一覧 |
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
  type: "http" | "sse" | "stdio" | "builtin" | "openapi";  // タイプ
  url: string | null;                       // URL（http/sseの場合）
  command: string | null;                   // コマンド（stdioの場合）
  args: string[] | null;                    // 引数（stdioの場合）
  env: Record<string, string> | null;       // 環境変数
  headers_template: Record<string, string> | null;  // ヘッダーテンプレート
  allowed_tools: string[] | null;           // 許可するツール名リスト
  tools: McpToolDefinition[] | null;        // ツール定義（builtinの場合）
  description: string | null;               // 説明
  openapi_spec: object | null;              // OpenAPI仕様（openapiの場合）
  openapi_base_url: string | null;          // OpenAPIベースURL
  status: "active" | "inactive";            // ステータス
  created_at: string;                       // 作成日時
  updated_at: string;                       // 更新日時
}
```

### McpToolDefinition

```typescript
interface McpToolDefinition {
  name: string;                    // ツール名
  description: string;             // ツールの説明
  input_schema: McpToolInputSchema; // 入力スキーマ
}

interface McpToolInputSchema {
  type: string;                              // "object"
  properties: Record<string, PropertyDef>;   // プロパティ定義
  required: string[] | null;                 // 必須プロパティ
}

interface PropertyDef {
  type: string;          // "string", "number", "boolean", etc.
  description?: string;  // プロパティの説明
  enum?: string[];       // 列挙値
  default?: any;         // デフォルト値
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

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "mcp_server_id": "mcp-001",
    "tenant_id": "acme-corp",
    "name": "servicenow",
    "display_name": "ServiceNow API",
    "type": "http",
    "url": "https://instance.service-now.com/api/now",
    "command": null,
    "args": null,
    "env": null,
    "headers_template": {
      "Authorization": "Bearer ${servicenowToken}"
    },
    "allowed_tools": ["search_incidents", "create_ticket"],
    "tools": null,
    "description": "ServiceNowとの連携",
    "openapi_spec": null,
    "openapi_base_url": null,
    "status": "active",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  }
]
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/mcp-servers" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/mcp-servers/builtin

利用可能なビルトインMCPサーバーの一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "name": "filesystem",
    "display_name": "File System",
    "description": "ローカルファイルシステムへのアクセス",
    "tools": ["read_file", "write_file", "list_directory"]
  },
  {
    "name": "web",
    "display_name": "Web Access",
    "description": "Webページの取得",
    "tools": ["fetch_url"]
  }
]
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/mcp-servers/builtin" \
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
  type: "http" | "sse" | "stdio" | "builtin" | "openapi";  // タイプ（必須）
  url?: string;                             // URL（http/sseで必須、最大500文字）
  command?: string;                         // コマンド（stdioで必須、最大500文字）
  args?: string[];                          // 引数（stdioの場合）
  env?: Record<string, string>;             // 環境変数
  headers_template?: Record<string, string>; // ヘッダーテンプレート
  allowed_tools?: string[];                 // 許可するツール名リスト
  tools?: McpToolDefinition[];              // ツール定義（builtinで必須）
  description?: string;                     // 説明
  openapi_spec?: object;                    // OpenAPI仕様（openapiで必須）
  openapi_base_url?: string;                // OpenAPIベースURL（最大500文字）
}
```

### タイプ別の必須フィールド

| タイプ | 必須フィールド |
|--------|---------------|
| `http` | `url` |
| `sse` | `url` |
| `stdio` | `command` |
| `builtin` | `tools` |
| `openapi` | `openapi_spec` |

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
  "name": "custom-api",
  "display_name": "Custom API",
  "type": "http",
  "url": "https://api.custom.com/v1",
  ...
  "status": "active",
  "created_at": "2024-01-17T09:00:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

**エラー: 必須フィールド不足 (400 Bad Request)**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "タイプ 'http' にはURLが必要です"
  }
}
```

### curlの例

#### HTTP タイプ

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/mcp-servers" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "servicenow",
    "display_name": "ServiceNow API",
    "type": "http",
    "url": "https://instance.service-now.com/api/now",
    "headers_template": {
      "Authorization": "Bearer ${servicenowToken}"
    },
    "description": "ServiceNowとの連携"
  }'
```

#### stdio タイプ

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/mcp-servers" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "python-tool",
    "type": "stdio",
    "command": "/usr/bin/python3",
    "args": ["-m", "custom_mcp_server"],
    "env": {
      "PYTHONPATH": "/opt/tools"
    }
  }'
```

#### builtin タイプ

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/mcp-servers" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "custom-calculator",
    "display_name": "カスタム計算機",
    "type": "builtin",
    "tools": [
      {
        "name": "calculate",
        "description": "数式を計算します",
        "input_schema": {
          "type": "object",
          "properties": {
            "expression": {
              "type": "string",
              "description": "計算する数式"
            }
          },
          "required": ["expression"]
        }
      }
    ]
  }'
```

#### openapi タイプ

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/mcp-servers" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "petstore",
    "display_name": "Pet Store API",
    "type": "openapi",
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
    "openapi_base_url": "https://petstore.example.com/api"
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
  type?: string;
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers_template?: Record<string, string>;
  allowed_tools?: string[];
  tools?: McpToolDefinition[];
  description?: string;
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
    "display_name": "Updated ServiceNow API",
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
- `mcp__servicenow__search_incidents`
- `mcp__petstore__listPets`

ストリーミングの`init`イベントの`tools`配列で確認できます。

---

## 関連API

- [ストリーミングAPI](./04-streaming.md) - `tokens`でMCPサーバー認証情報を渡す
- [テナント管理API](./01-tenants.md) - テナント設定
