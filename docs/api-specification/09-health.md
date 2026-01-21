# ヘルスチェックAPI

システムの状態監視とKubernetes/ECS対応のヘルスチェックを提供するAPIです。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/` および `/health` |
| 認証 | **不要** |
| スコープ | グローバル |

これらのエンドポイントは認証なしでアクセス可能です。
Kubernetesのliveness/readinessプローブとして使用できます。

---

## エンドポイント一覧

| メソッド | パス | 説明 | 用途 |
|---------|------|------|------|
| GET | `/` | ルートエンドポイント | API情報確認 |
| GET | `/health` | 詳細ヘルスチェック | モニタリング |
| GET | `/health/live` | Liveness Probe | Kubernetes |
| GET | `/health/ready` | Readiness Probe | Kubernetes |

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
| `redis` | 非重要 | Redisキャッシュ |
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
    "redis": {
      "status": "healthy",
      "message": null,
      "latency_ms": 1.15
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
    "redis": {
      "status": "unhealthy",
      "message": "Connection refused",
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
    "redis": {
      "status": "healthy",
      "message": null,
      "latency_ms": 1.15
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
    "redis": {
      "status": "healthy",
      "message": null,
      "latency_ms": 1.15
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

Kubernetesのliveness probe用エンドポイント。
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

### Kubernetes設定例

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
```

### curlの例

```bash
curl -X GET "https://api.example.com/health/live"
```

---

## GET /health/ready

Kubernetesのreadiness probe用エンドポイント。
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

### Kubernetes設定例

```yaml
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 3
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
    metrics_path: '/health'
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

## ECS タスク定義例

```json
{
  "containerDefinitions": [
    {
      "name": "api",
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/health/ready || exit 1"],
        "interval": 10,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 30
      }
    }
  ]
}
```

---

## 関連API

- [概要](./00-overview.md) - API全体の情報
