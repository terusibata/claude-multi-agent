# セキュリティ設定

本番運用に向けたセキュリティ設定のガイドです。

## 目次

- [概要](#概要)
- [API認証](#api認証)
- [レート制限](#レート制限)
- [セキュリティヘッダー](#セキュリティヘッダー)
- [リクエストトレーシング](#リクエストトレーシング)
- [分散ロック](#分散ロック)
- [環境変数一覧](#環境変数一覧)

## 概要

本システムは内部通信用APIとして設計されていますが、本番運用に必要なセキュリティ機能を備えています。

### アーキテクチャ

```
┌─────────────────┐     ┌──────────────────────────────────────┐
│  フロントエンド  │────▶│  バックエンドAPI                      │
│  サーバー        │     │  ┌─────────────────────────────────┐ │
└─────────────────┘     │  │ ミドルウェアスタック              │ │
                        │  │ 1. トレーシング (X-Request-ID)   │ │
                        │  │ 2. API認証 (X-API-Key)          │ │
                        │  │ 3. レート制限 (Redis)            │ │
                        │  │ 4. CORS                         │ │
                        │  │ 5. セキュリティヘッダー           │ │
                        │  └─────────────────────────────────┘ │
                        └──────────────────────────────────────┘
```

## API認証

### 認証方式

以下のいずれかの方法でAPIキーを送信します：

```bash
# X-API-Key ヘッダー（推奨）
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/tenants

# Authorization ヘッダー
curl -H "Authorization: Bearer your-api-key" http://localhost:8000/api/tenants
```

### 設定

```bash
# .env
# カンマ区切りで複数のAPIキーを設定可能
API_KEYS=key1,key2,key3
```

### 認証スキップパス

以下のパスは認証なしでアクセス可能です：

| パス | 用途 |
|------|------|
| `/` | ルートエンドポイント |
| `/health` | ヘルスチェック |
| `/health/live` | Kubernetes liveness probe |
| `/health/ready` | Kubernetes readiness probe |
| `/docs` | OpenAPI ドキュメント（開発環境のみ） |
| `/redoc` | ReDoc ドキュメント（開発環境のみ） |
| `/openapi.json` | OpenAPI スキーマ |

### 注意事項

- `API_KEYS`が空の場合、認証は無効化されます（開発環境用）
- 本番環境では必ず`API_KEYS`を設定してください
- APIキーはタイミング攻撃に対して安全なハッシュ比較を使用しています

## レート制限

### 概要

攻撃対策用のレート制限です。正常利用では引っかからない緩めの設定を推奨します。

### 制限ロジック

| 条件 | 識別子 | 用途 |
|------|--------|------|
| `X-User-ID` + `X-Tenant-ID` あり | `user:{tenant}:{user}` | ユーザー単位の制限 |
| ヘッダーなし | `ip:{address}` | IP単位の制限（フォールバック） |

### 設定

```bash
# .env
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=100  # ウィンドウあたりのリクエスト数
RATE_LIMIT_PERIOD=60     # ウィンドウサイズ（秒）
```

### レスポンスヘッダー

すべてのレスポンスに以下のヘッダーが付与されます：

| ヘッダー | 説明 |
|----------|------|
| `X-RateLimit-Limit` | ウィンドウあたりの上限 |
| `X-RateLimit-Remaining` | 残りリクエスト数 |
| `X-RateLimit-Reset` | リセット時刻（Unix timestamp） |

### 制限超過時

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "リクエスト数が制限を超えました。しばらくしてから再試行してください。",
    "retry_after": 30
  }
}
```

HTTPステータス: `429 Too Many Requests`

### フロントエンドからの呼び出し例

```bash
curl -X POST http://localhost:8000/api/tenants/xxx/conversations/yyy/stream \
  -H "X-API-Key: your-api-key" \
  -H "X-Tenant-ID: tenant-123" \
  -H "X-User-ID: user-456" \
  -H "Content-Type: application/json" \
  -d '{"user_input": "Hello"}'
```

## セキュリティヘッダー

すべてのレスポンスに以下のセキュリティヘッダーが自動付与されます：

| ヘッダー | 値 | 目的 |
|----------|-----|------|
| `X-Content-Type-Options` | `nosniff` | MIMEスニッフィング防止 |
| `X-Frame-Options` | `DENY` | クリックジャッキング防止 |
| `X-XSS-Protection` | `1; mode=block` | XSSフィルター有効化 |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | リファラー制御 |
| `Content-Security-Policy` | `default-src 'self'` | CSP |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | 機能制限 |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | HSTS（有効時） |

### HSTS設定

```bash
# .env
HSTS_ENABLED=true
HSTS_MAX_AGE=31536000  # 1年（秒）
```

## リクエストトレーシング

### X-Request-ID

すべてのリクエストに一意のIDが付与され、ログとレスポンスで追跡可能です。

```bash
# リクエスト時にIDを指定可能
curl -H "X-Request-ID: my-trace-id-123" http://localhost:8000/api/tenants

# レスポンスヘッダーで確認
# X-Request-ID: my-trace-id-123
# X-Process-Time: 0.0234
```

### ログ出力

すべてのログに`request_id`が含まれます：

```json
{
  "event": "リクエスト受信",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "method": "POST",
  "path": "/api/tenants/xxx/conversations/yyy/stream",
  "tenant_id": "xxx",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## 分散ロック

### 概要

水平スケーリング環境でのデータ整合性を保証するため、Redisベースの分散ロックを使用しています。

### 用途

- 会話の同時実行防止
- リソースの排他制御

### 設定

```bash
# .env
REDIS_URL=redis://localhost:6379/0
REDIS_MAX_CONNECTIONS=20
```

### 動作

1. エージェント実行開始時にロック取得
2. 実行完了時にロック解放
3. タイムアウト時は自動解放（デフォルト: 10分）

## 環境変数一覧

### セキュリティ関連

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `API_KEYS` | (空) | APIキー（カンマ区切り） |
| `RATE_LIMIT_ENABLED` | `true` | レート制限の有効化 |
| `RATE_LIMIT_REQUESTS` | `100` | ウィンドウあたりのリクエスト数 |
| `RATE_LIMIT_PERIOD` | `60` | ウィンドウサイズ（秒） |
| `HSTS_ENABLED` | `true` | HSTSの有効化 |
| `HSTS_MAX_AGE` | `31536000` | HSTSの有効期間（秒） |

### CORS関連

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:3001` | 許可するオリジン |
| `CORS_METHODS` | `GET,POST,PUT,DELETE,OPTIONS` | 許可するメソッド |
| `CORS_HEADERS` | `Content-Type,Authorization,X-API-Key,X-Request-ID,X-Tenant-ID` | 許可するヘッダー |

### Redis関連

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis接続URL |
| `REDIS_MAX_CONNECTIONS` | `20` | 最大接続数 |

## 本番環境チェックリスト

- [ ] `API_KEYS`を設定
- [ ] `APP_ENV=production`を設定
- [ ] `CORS_ORIGINS`を本番ドメインに限定
- [ ] `HSTS_ENABLED=true`を確認
- [ ] Redisが正常に稼働していることを確認
- [ ] ヘルスチェックエンドポイントの監視を設定
