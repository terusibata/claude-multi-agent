# OpenAPI MCPサーバー登録サンプル

OpenAPI仕様をAPI経由で登録し、MCPツールとして利用するサンプルです。

## 使い方

### Step 1: MCPサーバーを登録

```bash
# servicenow-docs.jsonの内容をそのままPOST
curl -X POST \
  "http://localhost:8000/api/tenants/{tenant_id}/mcp-servers" \
  -H "Content-Type: application/json" \
  -d @servicenow-docs.json
```

レスポンス例：
```json
{
  "mcp_server_id": "abc123-def456-...",
  "name": "servicenow-docs",
  "type": "openapi",
  "status": "active",
  ...
}
```

### Step 2: エージェント設定にMCPサーバーを追加

```bash
curl -X PUT \
  "http://localhost:8000/api/tenants/{tenant_id}/agent-configs/{agent_config_id}" \
  -H "Content-Type: application/json" \
  -d '{
    "mcp_servers": ["<mcp_server_id>"]
  }'
```

### Step 3: エージェントを使用

登録が完了すると、エージェントは以下のツールを使用できます：

- `mcp__servicenow-docs__searchDocuments` - ドキュメント検索
- `mcp__servicenow-docs__getDocumentDetail` - ドキュメント詳細取得

## サンプルファイル

| ファイル | 説明 |
|---------|------|
| `servicenow-docs.json` | ServiceNowドキュメント検索API |

## 独自のAPIを登録する場合

以下の形式でJSONを作成してください：

```json
{
  "name": "my-api",
  "display_name": "My Custom API",
  "type": "openapi",
  "description": "APIの説明（エージェントに表示される）",
  "openapi_base_url": "https://api.example.com",
  "openapi_spec": {
    "openapi": "3.1.1",
    "info": {
      "title": "My API",
      "version": "1.0.0"
    },
    "paths": {
      "/endpoint": {
        "get": {
          "operationId": "myOperation",
          "summary": "操作の概要",
          "description": "詳細な説明",
          "parameters": [...]
        }
      }
    }
  }
}
```

### 重要なポイント

1. **operationId**: 各エンドポイントには一意の`operationId`を設定（ツール名になる）
2. **description**: エージェントがツールを選択する際の判断材料になる
3. **parameters**: `in: query`はクエリパラメータ、`in: path`はパスパラメータ
4. **openapi_base_url**: 省略時は`servers[0].url`を使用

## 認証が必要なAPIの場合

```json
{
  "name": "authenticated-api",
  "type": "openapi",
  "headers_template": {
    "Authorization": "Bearer ${api_token}"
  },
  "openapi_spec": { ... }
}
```

実行時に`tokens`パラメータで値を渡す：
```json
{
  "tokens": {
    "api_token": "your-secret-token"
  }
}
```
