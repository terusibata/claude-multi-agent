# ヘルスチェックAPI

システムの状態監視とヘルスチェックを提供するAPIです。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/` および `/health` |
| 認証 | **不要** |
| スコープ | グローバル |

これらのエンドポイントは認証なしでアクセス可能です。
liveness/readinessプローブとして使用できます。

---

## エンドポイント一覧

| メソッド | パス | 説明 | 用途 |
|---------|------|------|------|
| GET | `/` | ルートエンドポイント | API情報確認 |
| GET | `/health` | 詳細ヘルスチェック | モニタリング |
| GET | `/health/live` | Liveness Probe | ヘルスチェック |
| GET | `/health/ready` | Readiness Probe | ヘルスチェック |
| GET | `/metrics` | Prometheusメトリクス | メトリクス収集 |

---

## データ型

### HealthResponse

```typescript
interface HealthResponse {
  status: "healthy" | "degraded" | "unhealthy";  // 全体ステータス
  version: string;                                // アプリケーションバージョン
  environment: string;                            // 環境（development/staging/production）
  timestamp: string;                              // チェック時刻（ISO 8601）
  checks: Record<string, ComponentHealth>;        // コンポーネント別チェック結果
}

interface ComponentHealth {
  status: "healthy" | "degraded" | "unhealthy";  // コンポーネントステータス
  message: string | null;                         // メッセージ（エラー時）
  latency_ms: number | null;                      // レイテンシ（ミリ秒）
}
```

### ステータスの意味

| ステータス | 説明 |
|-----------|------|
| `healthy` | 正常動作中 |
| `degraded` | 一部機能が低下（非重要コンポーネントの障害） |
| `unhealthy` | 異常状態（重要コンポーネントの障害） |

### 重要/非重要コンポーネント

| コンポーネント | 重要度 | 説明 |
|---------------|--------|------|
| `database` | **重要** | PostgreSQLデータベース |
| `s3` | 非重要 | AWS S3ストレージ |

重要コンポーネントが`unhealthy`の場合、全体ステータスも`unhealthy`になります。

---

## GET /

ルートエンドポイント。APIの基本情報を返します。

### レスポンス

**成功時 (200 OK)**

```json
{
  "name": "AIエージェントバックエンド",
  "version": "1.0.0",
  "docs_url": "/docs"
}
```

**注意**: `docs_url`は開発環境でのみ返されます。本番環境では`null`です。

### curlの例

```bash
curl -X GET "https://api.example.com/"
```

---

## GET /health

全コンポーネントの状態を確認する詳細ヘルスチェック。

### レスポンス

**成功時 - 全て正常 (200 OK)**

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "environment": "production",
  "timestamp": "2024-01-15T10:30:00.123Z",
  "checks": {
    "database": {
      "status": "healthy",
      "message": null,
      "latency_ms": 5.23
    },
    "s3": {
      "status": "healthy",
      "message": null,
      "latency_ms": 45.67
    }
  }
}
```

**一部障害時 - Degraded (200 OK)**

```json
{
  "status": "degraded",
  "version": "1.0.0",
  "environment": "production",
  "timestamp": "2024-01-15T10:30:00.123Z",
  "checks": {
    "database": {
      "status": "healthy",
      "message": null,
      "latency_ms": 5.23
    },
    "s3": {
      "status": "unhealthy",
      "message": "Connection refused",
      "latency_ms": null
    }
  }
}
```

**重要コンポーネント障害時 - Unhealthy (200 OK)**

```json
{
  "status": "unhealthy",
  "version": "1.0.0",
  "environment": "production",
  "timestamp": "2024-01-15T10:30:00.123Z",
  "checks": {
    "database": {
      "status": "unhealthy",
      "message": "Connection timed out",
      "latency_ms": null
    },
    "s3": {
      "status": "healthy",
      "message": null,
      "latency_ms": 45.67
    }
  }
}
```

**S3未設定時**

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "environment": "development",
  "timestamp": "2024-01-15T10:30:00.123Z",
  "checks": {
    "database": {
      "status": "healthy",
      "message": null,
      "latency_ms": 5.23
    },
    "s3": {
      "status": "healthy",
      "message": "S3未設定（スキップ）",
      "latency_ms": null
    }
  }
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/health"
```

---

## GET /health/live

Liveness probe用エンドポイント。
アプリケーションプロセスが生存しているかを確認します。

### 動作

- 常に`200 OK`を返します（プロセスが動作していれば成功）
- データベースやその他のサービスの状態は確認しません
- プロセスがハングした場合、応答がなくなります

### レスポンス

**成功時 (200 OK)**

```json
{
  "status": "alive"
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/health/live"
```

---

## GET /health/ready

Readiness probe用エンドポイント。
アプリケーションがトラフィックを受け入れる準備ができているかを確認します。

### 動作

- データベース接続が確立されている場合に`200 OK`を返します
- データベース接続に失敗した場合は`503 Service Unavailable`を返します

### レスポンス

**準備完了時 (200 OK)**

```json
{
  "status": "ready"
}
```

**準備未完了時 (503 Service Unavailable)**

```json
{
  "detail": "Service not ready"
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/health/ready"
```

---

## モニタリング推奨設定

### Prometheus / Grafana

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'ai-agent-backend'
    metrics_path: '/metrics'
    static_configs:
      - targets: ['api.example.com:8000']
```

### アラート設定例

| 条件 | アラートレベル |
|------|---------------|
| `/health/live` 応答なし | Critical |
| `/health/ready` が503 | Critical |
| `/health` が `unhealthy` | Critical |
| `/health` が `degraded` | Warning |
| `latency_ms` > 1000ms | Warning |

---

## Docker Compose 設定例

```yaml
services:
  api:
    image: ai-agent-backend:latest
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 30s
```

---

## GET /metrics

Prometheus形式でアプリケーションメトリクスを公開するエンドポイント。

### 概要

| 項目 | 値 |
|------|-----|
| パス | `/metrics` |
| 認証 | 開発環境: 不要、本番環境: 認証必要 |
| レスポンス形式 | `text/plain; version=0.0.4; charset=utf-8` (Prometheus text exposition format) |

### 公開メトリクス

#### HTTPメトリクス

| メトリクス名 | 型 | ラベル | 説明 |
|-------------|------|--------|------|
| `http_requests_total` | Counter | method, endpoint, status_code | HTTPリクエスト総数 |
| `http_request_duration_seconds` | Histogram | method, endpoint | HTTPリクエスト処理時間 |
| `active_connections` | Gauge | type | アクティブ接続数 |

#### インフラメトリクス

| メトリクス名 | 型 | ラベル | 説明 |
|-------------|------|--------|------|
| `db_pool_connections` | Gauge | state (idle/active/overflow) | DBコネクションプール状態 |
| `s3_operations_total` | Counter | operation, status | S3操作数 |
| `errors_total` | Counter | type, code | エラー総数 |

#### Bedrockメトリクス

| メトリクス名 | 型 | ラベル | 説明 |
|-------------|------|--------|------|
| `bedrock_requests_total` | Counter | model, status | Bedrock APIリクエスト数 |
| `bedrock_tokens_total` | Counter | model, type (input/output) | トークン使用量 |
| `agent_executions_total` | Counter | tenant_id, status | エージェント実行数 |
| `agent_execution_duration_seconds` | Histogram | tenant_id | エージェント実行時間 |

#### AgentCore Runtime メトリクス

| メトリクス名 | 型 | ラベル | 説明 |
|-------------|------|--------|------|
| `workspace_requests_total` | Counter | status | AgentCore呼び出し総数 |
| `workspace_s3_sync_errors_total` | Counter | direction | S3同期エラー数 |

### Prometheus scrape設定例

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'ai-agent-backend'
    metrics_path: '/metrics'
    scrape_interval: 15s
    static_configs:
      - targets: ['backend:8000']
```

### curlの例

```bash
curl -X GET "http://localhost:8000/metrics"
```

---

## 関連API

- [概要](./00-overview.md) - API全体の情報
