# 使用状況・コストAPI

トークン使用量とコストの監視・レポートを行うAPIです。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/tenants/{tenant_id}` |
| 認証 | 必要 |
| スコープ | テナント単位 |

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/usage` | 使用状況取得 |
| GET | `/api/tenants/{tenant_id}/usage/users/{user_id}` | ユーザー使用状況取得 |
| GET | `/api/tenants/{tenant_id}/usage/summary` | 使用状況サマリー取得 |
| GET | `/api/tenants/{tenant_id}/cost-report` | コストレポート取得 |
| GET | `/api/tenants/{tenant_id}/tool-logs` | ツール実行ログ取得 |

---

## データ型

### UsageLogResponse

```typescript
interface UsageLogResponse {
  usage_log_id: string;                  // 使用ログID
  tenant_id: string;                     // テナントID
  user_id: string;                       // ユーザーID
  model_id: string;                      // モデルID
  session_id: string | null;             // セッションID
  conversation_id: string | null;        // 会話ID
  input_tokens: number;                  // 入力トークン数
  output_tokens: number;                 // 出力トークン数
  cache_creation_5m_tokens: number;      // 5分キャッシュ作成トークン数
  cache_creation_1h_tokens: number;      // 1時間キャッシュ作成トークン数
  cache_read_tokens: number;             // キャッシュ読込トークン数
  total_tokens: number;                  // 合計トークン数
  cost_usd: number;                      // コスト（USD、Decimal）
  executed_at: string;                   // 実行日時
}
```

### UsageSummary

```typescript
interface UsageSummary {
  period: string;                        // 期間（例: "2024-01", "2024-01-15"）
  total_tokens: number;                  // 合計トークン数
  input_tokens: number;                  // 入力トークン数
  output_tokens: number;                 // 出力トークン数
  cache_creation_5m_tokens: number;      // 5分キャッシュ作成トークン数
  cache_creation_1h_tokens: number;      // 1時間キャッシュ作成トークン数
  cache_read_tokens: number;             // キャッシュ読込トークン数
  total_cost_usd: number;                // 合計コスト（USD、Decimal）
  execution_count: number;               // 実行回数
}
```

### CostReportResponse

```typescript
interface CostReportResponse {
  tenant_id: string;                     // テナントID
  from_date: string;                     // 開始日時
  to_date: string;                       // 終了日時
  total_cost_usd: number;                // 合計コスト（USD、Decimal）
  total_tokens: number;                  // 合計トークン数
  total_executions: number;              // 合計実行回数
  by_model: CostReportItem[];            // モデル別内訳
  by_user: UserCostItem[] | null;        // ユーザー別内訳
}

interface CostReportItem {
  model_id: string;                      // モデルID
  model_name: string;                    // モデル名
  total_tokens: number;                  // 合計トークン数
  input_tokens: number;                  // 入力トークン数
  output_tokens: number;                 // 出力トークン数
  cache_creation_5m_tokens: number;      // 5分キャッシュ作成トークン数
  cache_creation_1h_tokens: number;      // 1時間キャッシュ作成トークン数
  cache_read_tokens: number;             // キャッシュ読込トークン数
  cost_usd: number;                      // コスト（USD、Decimal）
  execution_count: number;               // 実行回数
}

interface UserCostItem {
  user_id: string;                       // ユーザーID
  total_tokens: number;                  // 合計トークン数
  cost_usd: number;                      // コスト（USD、Decimal）
  execution_count: number;               // 実行回数
}
```

### ToolLogResponse

```typescript
interface ToolLogResponse {
  tool_log_id: string;                   // ツールログID
  session_id: string;                    // セッションID
  conversation_id: string | null;        // 会話ID
  tool_name: string;                     // ツール名
  tool_use_id: string | null;            // ツール使用ID
  tool_input: object | null;             // ツール入力
  tool_output: object | null;            // ツール出力
  status: string;                        // ステータス
  execution_time_ms: number | null;      // 実行時間（ミリ秒）
  executed_at: string;                   // 実行日時
}
```

---

## GET /api/tenants/{tenant_id}/usage

テナントの使用状況ログを取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `user_id` | string | No | - | ユーザーIDフィルター |
| `from_date` | datetime | No | - | 開始日時（ISO 8601） |
| `to_date` | datetime | No | - | 終了日時（ISO 8601） |
| `limit` | integer | No | 100 | 取得件数（1-1000） |
| `offset` | integer | No | 0 | オフセット |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "usage_log_id": "usage-001",
    "tenant_id": "acme-corp",
    "user_id": "user-001",
    "model_id": "claude-sonnet-4",
    "session_id": "sess_abc123",
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "input_tokens": 5000,
    "output_tokens": 1500,
    "cache_creation_5m_tokens": 0,
    "cache_creation_1h_tokens": 0,
    "cache_read_tokens": 2000,
    "total_tokens": 8500,
    "cost_usd": "0.0285",
    "executed_at": "2024-01-15T10:30:00Z"
  }
]
```

### curlの例

```bash
# 全使用状況取得
curl -X GET "https://api.example.com/api/tenants/acme-corp/usage" \
  -H "X-API-Key: your_api_key"

# 日付範囲で絞り込み
curl -X GET "https://api.example.com/api/tenants/acme-corp/usage?from_date=2024-01-01T00:00:00Z&to_date=2024-01-31T23:59:59Z&limit=500" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/usage/users/{user_id}

特定ユーザーの使用状況ログを取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `user_id` | string | Yes | ユーザーID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `from_date` | datetime | No | - | 開始日時（ISO 8601） |
| `to_date` | datetime | No | - | 終了日時（ISO 8601） |
| `limit` | integer | No | 100 | 取得件数（1-1000） |
| `offset` | integer | No | 0 | オフセット |

### レスポンス

**成功時 (200 OK)**

`GET /usage` と同じ形式

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/usage/users/user-001" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/usage/summary

使用状況のサマリーを取得します。日/週/月単位でグループ化できます。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `from_date` | datetime | No | - | 開始日時（ISO 8601） |
| `to_date` | datetime | No | - | 終了日時（ISO 8601） |
| `group_by` | string | No | `day` | グループ化単位（`day` / `week` / `month`） |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "period": "2024-01-15",
    "total_tokens": 150000,
    "input_tokens": 100000,
    "output_tokens": 40000,
    "cache_creation_5m_tokens": 0,
    "cache_creation_1h_tokens": 0,
    "cache_read_tokens": 10000,
    "total_cost_usd": "4.25",
    "execution_count": 42
  },
  {
    "period": "2024-01-16",
    "total_tokens": 200000,
    "input_tokens": 130000,
    "output_tokens": 55000,
    "cache_creation_5m_tokens": 5000,
    "cache_creation_1h_tokens": 0,
    "cache_read_tokens": 10000,
    "total_cost_usd": "5.80",
    "execution_count": 58
  }
]
```

### curlの例

```bash
# 日別サマリー
curl -X GET "https://api.example.com/api/tenants/acme-corp/usage/summary?group_by=day&from_date=2024-01-01T00:00:00Z&to_date=2024-01-31T23:59:59Z" \
  -H "X-API-Key: your_api_key"

# 月別サマリー
curl -X GET "https://api.example.com/api/tenants/acme-corp/usage/summary?group_by=month" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/cost-report

コストレポートを生成します。モデル別・ユーザー別の内訳を確認できます。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `from_date` | datetime | **Yes** | - | 開始日時（ISO 8601） |
| `to_date` | datetime | **Yes** | - | 終了日時（ISO 8601） |
| `model_id` | string | No | - | モデルIDフィルター |
| `user_id` | string | No | - | ユーザーIDフィルター |

### レスポンス

**成功時 (200 OK)**

```json
{
  "tenant_id": "acme-corp",
  "from_date": "2024-01-01T00:00:00Z",
  "to_date": "2024-01-31T23:59:59Z",
  "total_cost_usd": "150.25",
  "total_tokens": 5000000,
  "total_executions": 1250,
  "by_model": [
    {
      "model_id": "claude-sonnet-4",
      "model_name": "Claude Sonnet 4",
      "total_tokens": 3500000,
      "input_tokens": 2500000,
      "output_tokens": 800000,
      "cache_creation_5m_tokens": 50000,
      "cache_creation_1h_tokens": 0,
      "cache_read_tokens": 150000,
      "cost_usd": "95.50",
      "execution_count": 850
    },
    {
      "model_id": "claude-opus-4",
      "model_name": "Claude Opus 4",
      "total_tokens": 1500000,
      "input_tokens": 1000000,
      "output_tokens": 400000,
      "cache_creation_5m_tokens": 20000,
      "cache_creation_1h_tokens": 0,
      "cache_read_tokens": 80000,
      "cost_usd": "54.75",
      "execution_count": 400
    }
  ],
  "by_user": [
    {
      "user_id": "user-001",
      "total_tokens": 2000000,
      "cost_usd": "60.00",
      "execution_count": 500
    },
    {
      "user_id": "user-002",
      "total_tokens": 1500000,
      "cost_usd": "45.00",
      "execution_count": 375
    }
  ]
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/cost-report?from_date=2024-01-01T00:00:00Z&to_date=2024-01-31T23:59:59Z" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/tool-logs

ツール実行ログを取得します。MCPツールを含むすべてのツール実行の詳細を確認できます。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `session_id` | string | No | - | セッションIDフィルター |
| `tool_name` | string | No | - | ツール名フィルター |
| `from_date` | datetime | No | - | 開始日時（ISO 8601） |
| `to_date` | datetime | No | - | 終了日時（ISO 8601） |
| `limit` | integer | No | 100 | 取得件数（1-1000） |
| `offset` | integer | No | 0 | オフセット |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "tool_log_id": "tool-001",
    "session_id": "sess_abc123",
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "tool_name": "Read",
    "tool_use_id": "tu_abc123",
    "tool_input": {
      "file_path": "/workspace/data.csv"
    },
    "tool_output": {
      "content": "id,name,value\n1,Alice,100\n..."
    },
    "status": "success",
    "execution_time_ms": 150,
    "executed_at": "2024-01-15T10:30:05Z"
  },
  {
    "tool_log_id": "tool-002",
    "session_id": "sess_abc123",
    "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
    "tool_name": "mcp__servicenow__search_incidents",
    "tool_use_id": "tu_def456",
    "tool_input": {
      "query": "priority=1"
    },
    "tool_output": {
      "incidents": [...]
    },
    "status": "success",
    "execution_time_ms": 2500,
    "executed_at": "2024-01-15T10:30:10Z"
  }
]
```

### curlの例

```bash
# 特定セッションのツールログ
curl -X GET "https://api.example.com/api/tenants/acme-corp/tool-logs?session_id=sess_abc123" \
  -H "X-API-Key: your_api_key"

# 特定ツールのログ
curl -X GET "https://api.example.com/api/tenants/acme-corp/tool-logs?tool_name=Read&limit=50" \
  -H "X-API-Key: your_api_key"
```

---

## コスト計算について

コストは以下の式で計算されます：

```
コスト (USD) =
  (入力トークン × 入力単価 / 1000) +
  (出力トークン × 出力単価 / 1000) +
  (5分キャッシュ作成トークン × 5分キャッシュ単価 / 1000) +
  (1時間キャッシュ作成トークン × 1時間キャッシュ単価 / 1000) +
  (キャッシュ読込トークン × キャッシュ読込単価 / 1000)
```

各モデルの単価は[モデル管理API](./02-models.md)で設定されています。

---

## 関連API

- [モデル管理API](./02-models.md) - モデルの料金設定
- [会話管理API](./03-conversations.md) - 会話情報
- [ストリーミングAPI](./04-streaming.md) - `done`イベントで使用量を取得
