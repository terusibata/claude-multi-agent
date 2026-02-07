# API仕様書 - 概要

このドキュメントは、AIエージェントバックエンドAPIの完全な仕様書です。
フロントエンド（Next.js）開発者が実装に必要なすべての情報を提供します。

## 目次

| ファイル | 説明 |
|---------|------|
| [01-tenants.md](./01-tenants.md) | テナント管理API |
| [02-models.md](./02-models.md) | モデル管理API |
| [03-conversations.md](./03-conversations.md) | 会話管理API |
| [04-streaming.md](./04-streaming.md) | ストリーミング実行API（SSEイベント） |
| [05-skills.md](./05-skills.md) | Agent Skills管理API |
| [06-mcp-servers.md](./06-mcp-servers.md) | MCPサーバー管理API |
| [07-workspace.md](./07-workspace.md) | ワークスペースAPI |
| [08-usage.md](./08-usage.md) | 使用状況・コストAPI |
| [09-health.md](./09-health.md) | ヘルスチェックAPI |

---

## 技術スタック

- **フレームワーク**: FastAPI (Python)
- **データベース**: PostgreSQL (非同期SQLAlchemy)
- **キャッシュ**: Redis
- **ストレージ**: AWS S3
- **LLM**: AWS Bedrock (Claude)
- **ストリーミング**: Server-Sent Events (SSE)

---

## ベースURL

```
https://{your-domain}/api
```

ローカル開発環境:
```
http://localhost:8000/api
```

---

## 認証

### 認証方式

APIへのアクセスにはAPIキー認証が必要です。以下の2つの方法で認証できます。

#### 方法1: X-API-Key ヘッダー（推奨）

```http
X-API-Key: your_api_key_here
```

#### 方法2: Authorization Bearer ヘッダー

```http
Authorization: Bearer your_api_key_here
```

### 認証不要エンドポイント

以下のエンドポイントは認証なしでアクセス可能です：

| パス | 説明 |
|------|------|
| `/` | ルートエンドポイント |
| `/health` | 詳細ヘルスチェック |
| `/health/live` | Liveness Probe |
| `/health/ready` | Readiness Probe |
| `/metrics` | Prometheusメトリクス |
| `/docs` | Swagger UI（開発環境のみ） |
| `/redoc` | ReDoc（開発環境のみ） |
| `/openapi.json` | OpenAPI仕様 |

### 認証エラーレスポンス

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "APIキーが必要です"
  }
}
```

または

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "無効なAPIキーです"
  }
}
```

**HTTPステータスコード**: `401 Unauthorized`

---

## 共通ヘッダー

### リクエストヘッダー

| ヘッダー | 必須 | 説明 |
|---------|------|------|
| `X-API-Key` | Yes* | APIキー（認証方法1） |
| `Authorization` | Yes* | `Bearer {api_key}`（認証方法2） |
| `Content-Type` | No | リクエストボディの形式。通常は `application/json` |
| `X-Request-ID` | No | リクエスト追跡用ID（指定しない場合は自動生成） |

*いずれか一方が必須

### レスポンスヘッダー

| ヘッダー | 説明 |
|---------|------|
| `X-Request-ID` | リクエスト追跡用ID |
| `X-Process-Time` | 処理時間（ミリ秒） |
| `X-RateLimit-Limit` | レート制限の最大リクエスト数 |
| `X-RateLimit-Remaining` | 残りリクエスト数 |
| `X-RateLimit-Reset` | レート制限リセット時刻（Unix時間） |

---

## レート制限

デフォルト設定：
- **リクエスト数**: 100リクエスト
- **期間**: 60秒

レート制限を超えた場合のレスポンス:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "リクエスト制限を超えました。しばらく待ってから再試行してください。"
  }
}
```

**HTTPステータスコード**: `429 Too Many Requests`

---

## 共通レスポンス形式

### 成功レスポンス

単一リソース:
```json
{
  "tenant_id": "example-tenant",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

リストレスポンス:
```json
[
  { "id": "1", "name": "Item 1" },
  { "id": "2", "name": "Item 2" }
]
```

### エラーレスポンス形式

すべてのエラーは統一された形式で返されます（RFC 7807準拠）:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "ユーザー向けエラーメッセージ",
    "details": [
      {
        "field": "email",
        "message": "有効なメールアドレスを入力してください",
        "code": "invalid_format"
      }
    ],
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "timestamp": "2024-01-15T10:30:00Z"
  }
}
```

### エラーコード一覧

| コード | HTTPステータス | 説明 |
|--------|---------------|------|
| `UNAUTHORIZED` | 401 | 認証エラー |
| `FORBIDDEN` | 403 | アクセス権限エラー |
| `NOT_FOUND` | 404 | リソースが見つからない |
| `CONFLICT` | 409 | リソースの競合 |
| `VALIDATION_ERROR` | 400/422 | 入力値エラー |
| `INVALID_INPUT` | 400 | 不正な入力 |
| `RATE_LIMIT_EXCEEDED` | 429 | レート制限超過 |
| `SECURITY_ERROR` | 403 | セキュリティエラー |
| `PATH_TRAVERSAL` | 403 | パストラバーサル検出 |
| `RESOURCE_LOCKED` | 409 | リソースがロック中 |
| `RESOURCE_INACTIVE` | 400 | リソースが非アクティブ |
| `INTERNAL_ERROR` | 500 | 内部サーバーエラー |
| `SERVICE_UNAVAILABLE` | 503 | サービス利用不可 |
| `EXTERNAL_SERVICE_ERROR` | 502 | 外部サービスエラー |

---

## ステータス値

### テナントステータス

| 値 | 説明 |
|----|------|
| `active` | アクティブ（利用可能） |
| `inactive` | 非アクティブ（利用不可） |

### モデルステータス

| 値 | 説明 |
|----|------|
| `active` | アクティブ（利用可能） |
| `deprecated` | 非推奨（新規実行不可） |

### 会話ステータス

| 値 | 説明 |
|----|------|
| `active` | アクティブ（継続可能） |
| `archived` | アーカイブ済み |

### Skill/MCPサーバーステータス

| 値 | 説明 |
|----|------|
| `active` | アクティブ（利用可能） |
| `inactive` | 非アクティブ（利用不可） |

---

## 日時形式

すべての日時はISO 8601形式（UTC）で返されます:

```
2024-01-15T10:30:00Z
```

リクエスト時も同じ形式で送信してください。

---

## ページネーション

リスト取得APIでは、以下のクエリパラメータでページネーションを制御できます:

| パラメータ | 型 | デフォルト | 最大値 | 説明 |
|-----------|-----|-----------|--------|------|
| `limit` | integer | 50 | 100-1000 | 取得件数 |
| `offset` | integer | 0 | - | オフセット |

---

## マルチテナント設計

このAPIはマルチテナント対応で設計されています。
ほとんどのリソースは`tenant_id`にスコープされています。

### テナントスコープのリソース

- 会話 (`conversations`)
- Skills (`skills`)
- MCPサーバー (`mcp-servers`)
- 使用状況ログ (`usage`)
- ワークスペースファイル (`files`)

### グローバルリソース

- モデル定義 (`models`)
- テナント (`tenants`)

---

## curlの基本例

```bash
# テナント一覧取得
curl -X GET "https://api.example.com/api/tenants" \
  -H "X-API-Key: your_api_key"

# テナント作成
curl -X POST "https://api.example.com/api/tenants" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "new-tenant",
    "system_prompt": "あなたは親切なアシスタントです。"
  }'
```

---

## 次のステップ

各エンドポイントの詳細仕様は、以下のドキュメントを参照してください：

1. [テナント管理API](./01-tenants.md) - テナントのCRUD操作
2. [モデル管理API](./02-models.md) - AIモデル定義の管理
3. [会話管理API](./03-conversations.md) - 会話のCRUD操作
4. [ストリーミングAPI](./04-streaming.md) - **最重要** SSEによるリアルタイム実行
5. [Skills管理API](./05-skills.md) - Agent Skillsの管理
6. [MCPサーバー管理API](./06-mcp-servers.md) - MCPサーバー設定
7. [ワークスペースAPI](./07-workspace.md) - ファイル管理
8. [使用状況API](./08-usage.md) - 使用量・コストレポート
9. [ヘルスチェックAPI](./09-health.md) - システム監視
