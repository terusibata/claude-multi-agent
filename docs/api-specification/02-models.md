# モデル管理API

AIモデル定義の管理を行うAPIです。モデルの料金設定やステータス管理が可能です。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/models` |
| 認証 | 必要 |
| スコープ | グローバル |

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/models` | モデル一覧取得 |
| POST | `/api/models` | モデル定義作成 |
| GET | `/api/models/{model_id}` | モデル詳細取得 |
| PUT | `/api/models/{model_id}` | モデル定義更新 |
| PATCH | `/api/models/{model_id}/status` | ステータス変更 |
| DELETE | `/api/models/{model_id}` | モデル定義削除 |

---

## データ型

### ModelResponse

```typescript
interface ModelResponse {
  model_id: string;                    // 内部管理ID（プライマリキー）
  display_name: string;                // UIで表示する名称
  bedrock_model_id: string;            // AWS BedrockのモデルID
  model_region: string | null;         // モデルのデプロイリージョン

  // Context Window設定
  context_window: number;              // Context Window上限（トークン）
  max_output_tokens: number;           // 最大出力トークン数
  supports_extended_context: boolean;  // 拡張Context Window（1M等）対応可否
  extended_context_window: number | null; // 拡張Context Window上限

  // 料金設定
  input_token_price: string;           // 入力トークン単価 (USD/1Kトークン)
  output_token_price: string;          // 出力トークン単価 (USD/1Kトークン)
  cache_creation_5m_price: string;     // 5分キャッシュ作成単価 (USD/1Kトークン)
  cache_creation_1h_price: string;     // 1時間キャッシュ作成単価 (USD/1Kトークン)
  cache_read_price: string;            // キャッシュ読込単価 (USD/1Kトークン)

  status: "active" | "deprecated";     // ステータス
  created_at: string;                  // 作成日時（ISO 8601）
  updated_at: string;                  // 更新日時（ISO 8601）
}
```

### Context Window設定の説明

| フィールド | 説明 | デフォルト値 |
|-----------|------|-------------|
| `context_window` | モデルのContext Window上限（入力+出力の合計） | 200000 |
| `max_output_tokens` | 1回のレスポンスで生成可能な最大トークン数 | 64000 |
| `supports_extended_context` | 1M Context Window等の拡張機能に対応しているか | false |
| `extended_context_window` | 拡張Context Window使用時の上限 | null |

**Context Windowの使用例:**

- Claude Sonnet 4.5: 200,000トークン（拡張: 1,000,000トークン）
- Claude Opus 4.5: 200,000トークン
- Claude Haiku 4.5: 200,000トークン

### 料金体系の説明

| フィールド | 説明 | 一般的な価格比率 |
|-----------|------|-----------------|
| `input_token_price` | 入力トークンの単価 | 基準価格 |
| `output_token_price` | 出力トークンの単価 | 入力の3-5倍程度 |
| `cache_creation_5m_price` | 5分間有効なキャッシュ作成コスト | 入力の1.25倍 |
| `cache_creation_1h_price` | 1時間有効なキャッシュ作成コスト | 入力の2.0倍 |
| `cache_read_price` | キャッシュ読み取りコスト | 入力の0.1倍 |

---

## GET /api/models

登録されている全モデル定義を取得します。

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `status` | string | No | - | ステータスフィルター (`active` / `deprecated`) |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "model_id": "claude-sonnet-4",
    "display_name": "Claude Sonnet 4",
    "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "model_region": "us-west-2",
    "input_token_price": "0.003",
    "output_token_price": "0.015",
    "cache_creation_5m_price": "0.00375",
    "cache_creation_1h_price": "0.006",
    "cache_read_price": "0.0003",
    "status": "active",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  },
  {
    "model_id": "claude-opus-4",
    "display_name": "Claude Opus 4",
    "bedrock_model_id": "us.anthropic.claude-opus-4-20250514-v1:0",
    "model_region": "us-west-2",
    "input_token_price": "0.015",
    "output_token_price": "0.075",
    "cache_creation_5m_price": "0.01875",
    "cache_creation_1h_price": "0.03",
    "cache_read_price": "0.0015",
    "status": "active",
    "created_at": "2024-01-16T14:00:00Z",
    "updated_at": "2024-01-16T14:00:00Z"
  }
]
```

### curlの例

```bash
# 全モデル取得
curl -X GET "https://api.example.com/api/models" \
  -H "X-API-Key: your_api_key"

# アクティブなモデルのみ取得
curl -X GET "https://api.example.com/api/models?status=active" \
  -H "X-API-Key: your_api_key"
```

---

## POST /api/models

新しいモデル定義を作成します。

### リクエストボディ

```typescript
interface ModelCreate {
  model_id: string;                     // 内部管理ID（必須、一意）
  display_name: string;                 // 表示名（必須、最大200文字）
  bedrock_model_id: string;             // AWS BedrockモデルID（必須、最大200文字）
  model_region?: string;                // デプロイリージョン（最大50文字）
  input_token_price?: string;           // 入力トークン単価（デフォルト: "0"）
  output_token_price?: string;          // 出力トークン単価（デフォルト: "0"）
  cache_creation_5m_price?: string;     // 5分キャッシュ作成単価（デフォルト: "0"）
  cache_creation_1h_price?: string;     // 1時間キャッシュ作成単価（デフォルト: "0"）
  cache_read_price?: string;            // キャッシュ読込単価（デフォルト: "0"）
}
```

| フィールド | 型 | 必須 | 制限 | 説明 |
|-----------|-----|------|------|------|
| `model_id` | string | Yes | 最大100文字 | 内部管理ID（一意） |
| `display_name` | string | Yes | 最大200文字 | UIで表示する名称 |
| `bedrock_model_id` | string | Yes | 最大200文字 | AWS BedrockのモデルID |
| `model_region` | string | No | 最大50文字 | モデルのデプロイリージョン |
| `input_token_price` | Decimal | No | - | 入力トークン単価 (USD/1Kトークン) |
| `output_token_price` | Decimal | No | - | 出力トークン単価 (USD/1Kトークン) |
| `cache_creation_5m_price` | Decimal | No | - | 5分キャッシュ作成単価 |
| `cache_creation_1h_price` | Decimal | No | - | 1時間キャッシュ作成単価 |
| `cache_read_price` | Decimal | No | - | キャッシュ読込単価 |

### レスポンス

**成功時 (201 Created)**

```json
{
  "model_id": "claude-haiku-3",
  "display_name": "Claude Haiku 3",
  "bedrock_model_id": "us.anthropic.claude-3-haiku-20240307-v1:0",
  "model_region": "us-west-2",
  "input_token_price": "0.00025",
  "output_token_price": "0.00125",
  "cache_creation_5m_price": "0.0003125",
  "cache_creation_1h_price": "0.0005",
  "cache_read_price": "0.000025",
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
    "message": "モデル 'claude-haiku-3' は既に存在します",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

### curlの例

```bash
curl -X POST "https://api.example.com/api/models" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "claude-haiku-3",
    "display_name": "Claude Haiku 3",
    "bedrock_model_id": "us.anthropic.claude-3-haiku-20240307-v1:0",
    "model_region": "us-west-2",
    "input_token_price": "0.00025",
    "output_token_price": "0.00125",
    "cache_creation_5m_price": "0.0003125",
    "cache_creation_1h_price": "0.0005",
    "cache_read_price": "0.000025"
  }'
```

---

## GET /api/models/{model_id}

指定したIDのモデル定義を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `model_id` | string | Yes | モデルID |

### レスポンス

**成功時 (200 OK)**

```json
{
  "model_id": "claude-sonnet-4",
  "display_name": "Claude Sonnet 4",
  "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
  "model_region": "us-west-2",
  "input_token_price": "0.003",
  "output_token_price": "0.015",
  "cache_creation_5m_price": "0.00375",
  "cache_creation_1h_price": "0.006",
  "cache_read_price": "0.0003",
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
    "message": "モデル 'unknown-model' が見つかりません",
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/models/claude-sonnet-4" \
  -H "X-API-Key: your_api_key"
```

---

## PUT /api/models/{model_id}

モデル定義を更新します（料金変更等）。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `model_id` | string | Yes | モデルID |

### リクエストボディ

```typescript
interface ModelUpdate {
  display_name?: string;                // 表示名
  bedrock_model_id?: string;            // AWS BedrockモデルID
  model_region?: string;                // デプロイリージョン
  input_token_price?: string;           // 入力トークン単価
  output_token_price?: string;          // 出力トークン単価
  cache_creation_5m_price?: string;     // 5分キャッシュ作成単価
  cache_creation_1h_price?: string;     // 1時間キャッシュ作成単価
  cache_read_price?: string;            // キャッシュ読込単価
  status?: "active" | "deprecated";     // ステータス
}
```

**注意**: 指定したフィールドのみ更新されます。

### レスポンス

**成功時 (200 OK)**

```json
{
  "model_id": "claude-sonnet-4",
  "display_name": "Claude Sonnet 4 (Updated)",
  "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
  "model_region": "us-west-2",
  "input_token_price": "0.0035",
  "output_token_price": "0.0175",
  "cache_creation_5m_price": "0.004375",
  "cache_creation_1h_price": "0.007",
  "cache_read_price": "0.00035",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-17T11:00:00Z"
}
```

### curlの例

```bash
curl -X PUT "https://api.example.com/api/models/claude-sonnet-4" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Claude Sonnet 4 (Updated)",
    "input_token_price": "0.0035",
    "output_token_price": "0.0175"
  }'
```

---

## PATCH /api/models/{model_id}/status

モデルのステータスを変更します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `model_id` | string | Yes | モデルID |

### クエリパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `status` | string | Yes | 新しいステータス (`active` / `deprecated`) |

### ステータスの意味

| ステータス | 説明 |
|-----------|------|
| `active` | 利用可能。新規会話・実行で使用可能 |
| `deprecated` | 非推奨。既存の会話は継続可能だが、新規実行は不可 |

### レスポンス

**成功時 (200 OK)**

```json
{
  "model_id": "claude-sonnet-4",
  "display_name": "Claude Sonnet 4",
  "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
  "model_region": "us-west-2",
  "input_token_price": "0.003",
  "output_token_price": "0.015",
  "cache_creation_5m_price": "0.00375",
  "cache_creation_1h_price": "0.006",
  "cache_read_price": "0.0003",
  "status": "deprecated",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-17T12:00:00Z"
}
```

### curlの例

```bash
curl -X PATCH "https://api.example.com/api/models/claude-sonnet-4/status?status=deprecated" \
  -H "X-API-Key: your_api_key"
```

---

## DELETE /api/models/{model_id}

モデル定義を削除します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `model_id` | string | Yes | モデルID |

### 削除条件

以下の条件をすべて満たす場合のみ削除可能です：

1. テナントのデフォルトモデルとして使用されていない
2. 会話で使用されていない
3. 使用量ログに記録がない

### レスポンス

**成功時 (204 No Content)**

レスポンスボディなし

**エラー: 使用中 (409 Conflict)**

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "モデル 'claude-sonnet-4' は使用中のため削除できません",
    "details": {
      "tenants": ["acme-corp", "tech-startup"],
      "conversations": 42,
      "usage_logs": 156
    },
    "request_id": "req-123",
    "timestamp": "2024-01-17T09:00:00Z"
  }
}
```

### curlの例

```bash
curl -X DELETE "https://api.example.com/api/models/old-model" \
  -H "X-API-Key: your_api_key"
```

---

## AWS Bedrock モデルID一覧（参考）

よく使用されるAWS BedrockのモデルID:

| モデル | Bedrock Model ID |
|--------|------------------|
| Claude Sonnet 4.5 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| Claude Sonnet 4 | `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| Claude Opus 4 | `us.anthropic.claude-opus-4-20250514-v1:0` |
| Claude 3.5 Sonnet | `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| Claude 3 Haiku | `anthropic.claude-3-haiku-20240307-v1:0` |

---

## 関連API

- [テナント管理API](./01-tenants.md) - テナントのデフォルトモデル設定
- [会話管理API](./03-conversations.md) - 会話で使用するモデル
- [使用状況API](./08-usage.md) - モデルごとの使用量・コスト
