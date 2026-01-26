# 会話専用ワークスペース機能（S3版）

## 概要

会話専用ワークスペースは、AIエージェントがファイル操作を行う際に、会話ごとに独立したファイル空間を提供する機能です。ファイルはAmazon S3に保存され、APIサーバー経由でのみアクセス可能です。

### アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────┐
│  フロントエンド                                                  │
│                                                                 │
│  ファイル送信: POST /conversations/{id}/stream (multipart)      │
│  ファイル取得: GET /conversations/{id}/files/download?path=xxx  │
│               → バイナリデータが返る                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  バックエンド API                                               │
│                                                                 │
│  S3との通信はすべてここで行う                                    │
│  フロントエンドはS3に直接アクセスしない                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Amazon S3 (完全プライベート)                                   │
│                                                                 │
│  - パブリックアクセス: すべてブロック                            │
│  - CORS: 不要                                                   │
│  - アクセス: IAM認証のみ                                        │
└─────────────────────────────────────────────────────────────────┘
```

### ユースケース

| ユースケース | ワークスペース | 説明 |
|-------------|--------------|------|
| ドキュメント分析・要約 | **必要** | ユーザーがPDFやExcelをアップロードし、AIが分析結果をファイル出力 |
| コード生成・レビュー | **必要** | AIがコードファイルを生成し、ユーザーがダウンロード |
| データ変換 | **必要** | CSV→JSON変換など、ファイル入出力が必要なタスク |
| 翻訳・校正 | 不要 | テキストの入出力のみで完結 |
| 質問応答 | 不要 | 会話のみで完結 |

---

## 事前準備

### 1. S3バケットの作成

```
設定:
├── バケット名: your-app-workspaces（任意）
├── リージョン: ap-northeast-1（任意）
├── パブリックアクセス: すべてブロック ✓
├── バージョニング: 有効（推奨）
├── CORS: 不要（サーバー経由のため）
└── 暗号化: SSE-S3
```

### 2. IAMポリシーの設定

AWS認証情報（`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`）に以下の権限が必要です：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:HeadObject"
      ],
      "Resource": [
        "arn:aws:s3:::your-app-workspaces",
        "arn:aws:s3:::your-app-workspaces/*"
      ]
    }
  ]
}
```

**注意**: AWS認証情報には **Bedrock** と **S3** の両方の権限が必要です。

### 3. 環境変数の設定

```bash
# S3ワークスペース設定
S3_BUCKET_NAME=your-app-workspaces
S3_WORKSPACE_PREFIX=workspaces/
```

### 4. ライフサイクルポリシー（推奨）

古いファイルの自動削除・移行を設定：

```
ルール名: workspace-lifecycle
プレフィックス: workspaces/
移行:
├── 7日後 → S3 標準-IA
├── 30日後 → Glacier Instant Retrieval
└── 90日後 → Glacier Deep Archive（または削除）
```

---

## 設定方法

### 会話作成時に有効化

会話を作成する際に `workspace_enabled: true` を指定してワークスペースを有効化します：

```bash
curl -X POST http://localhost:8000/api/tenants/tenant-001/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "model_id": "claude-sonnet-4",
    "workspace_enabled": true
  }'
```

レスポンス：
```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant-001",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "status": "active",
  "workspace_enabled": true,
  "created_at": "2024-01-01T00:00:00Z"
}
```

---

## ファイルアップロード（ストリーミングAPI）

ファイルのアップロードはストリーミングAPIで行います。

### エンドポイント

```
POST /api/tenants/{tenant_id}/conversations/{conversation_id}/stream
Content-Type: multipart/form-data
```

### リクエストパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `request_data` | string | ○ | StreamRequestのJSON文字列 |
| `files` | File[] | - | アップロードするファイル（複数可） |

### cURLの例

```bash
# ファイル添付付きで実行
curl -X POST "http://localhost:8000/api/tenants/tenant-001/conversations/550e8400-uuid/stream" \
  -H "Accept: text/event-stream" \
  -F 'request_data={
    "user_input": "このファイルを分析してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    }
  }' \
  -F "files=@/path/to/document.pdf" \
  -F "files=@/path/to/data.csv"
```

### JavaScriptの例

```javascript
const formData = new FormData();

// StreamRequestをJSON文字列として追加
const requestData = {
  user_input: "このファイルを分析してください",
  executor: {
    user_id: "user-001",
    name: "田中太郎",
    email: "tanaka@example.com"
  }
};
formData.append('request_data', JSON.stringify(requestData));

// ファイルを追加
formData.append('files', file1);
formData.append('files', file2);

const response = await fetch(
  `/api/tenants/${tenantId}/conversations/${conversationId}/stream`,
  {
    method: 'POST',
    body: formData,
    headers: {
      'Accept': 'text/event-stream'
    }
  }
);
```

ファイルがアップロードされると、自動的にS3に保存されます。

**注意**: ファイルをアップロードする場合、会話作成時に `workspace_enabled: true` を指定しておく必要があります。

---

## ファイル一覧取得

### エンドポイント

```
GET /api/tenants/{tenant_id}/conversations/{conversation_id}/files
```

### レスポンス例

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "files": [
    {
      "file_id": "a1b2c3d4-...",
      "file_path": "uploads/data.csv",
      "original_name": "data.csv",
      "file_size": 2048,
      "mime_type": "text/csv",
      "version": 1,
      "source": "user_upload",
      "is_presented": false,
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z"
    },
    {
      "file_id": "e5f6g7h8-...",
      "file_path": "outputs/analysis_result.json",
      "original_name": "analysis_result.json",
      "file_size": 512,
      "mime_type": "application/json",
      "version": 1,
      "source": "ai_created",
      "is_presented": true,
      "created_at": "2024-01-01T00:00:05Z",
      "updated_at": "2024-01-01T00:00:05Z"
    }
  ],
  "total_count": 2,
  "total_size": 2560
}
```

---

## ファイルダウンロード

### エンドポイント

```
GET /api/tenants/{tenant_id}/conversations/{conversation_id}/files/download?path=xxx
```

### クエリパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `path` | string | ○ | ファイルパス（ワークスペース内） |

### 使用例

```bash
# アップロードファイルをダウンロード
curl -O "http://localhost:8000/api/tenants/tenant-001/conversations/550e8400-uuid/files/download?path=uploads/data.csv"

# AI生成ファイルをダウンロード
curl -O "http://localhost:8000/api/tenants/tenant-001/conversations/550e8400-uuid/files/download?path=outputs/result.json"
```

### レスポンス

- `Content-Type`: ファイルのMIMEタイプ
- `Content-Disposition`: `attachment; filename="ファイル名"`
- Body: ファイルのバイナリデータ

---

## Presentedファイル

AIが作成したファイルのうち、ユーザーに提示したいものは自動的に「Presented」としてマークされます。

### Presentedファイル一覧取得

```
GET /api/tenants/{tenant_id}/conversations/{conversation_id}/files/presented
```

### レスポンス例

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "files": [
    {
      "file_id": "e5f6g7h8-...",
      "file_path": "outputs/analysis_result.xlsx",
      "original_name": "analysis_result.xlsx",
      "file_size": 1024,
      "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "version": 1,
      "source": "ai_created",
      "is_presented": true,
      "created_at": "2024-01-01T00:00:05Z"
    }
  ]
}
```

### 自動登録

AIが作成・変更したファイルは、実行完了時に自動的にPresentedファイルとして登録されます。ディレクトリの場所に関係なく、ローカルワークスペースからS3に同期されたすべてのファイルが対象です。

---

## S3キー構造

```
{S3_WORKSPACE_PREFIX}/
└── {tenant_id}/
    └── {conversation_id}/
        ├── uploads/      # ユーザーアップロードファイル
        └── ...           # AIが作成したファイル（任意の場所）
```

例：
```
workspaces/tenant-001/550e8400-uuid/uploads/data.csv
workspaces/tenant-001/550e8400-uuid/result.json
workspaces/tenant-001/550e8400-uuid/analysis/report.xlsx
```

**注意**: AIはワークスペース直下や任意のサブディレクトリにファイルを作成できます。`uploads/` はユーザーアップロード用の予約ディレクトリです。

---

## エージェント実行フロー

1. **会話作成**: `workspace_enabled: true` で会話を作成
2. **ファイルアップロード**: `/stream` APIでファイルをS3にアップロード
3. **S3→ローカル同期**: 実行前にS3から一時ローカルディレクトリにファイルを同期
4. **エージェント実行**: ローカルディレクトリでファイル操作を実行
5. **ローカル→S3同期**: 実行後にローカルからS3にファイルを同期
6. **AIファイル登録**: AIが作成・変更したファイルをPresentedファイルとして自動登録
7. **ローカルクリーンアップ**: 一時ローカルディレクトリを削除

---

## セキュリティ

### S3アクセス制御

- S3バケットは完全プライベート
- パブリックアクセスは完全にブロック
- APIサーバー経由でのみアクセス可能
- IAM認証による安全なアクセス

### テナント・会話分離

- テナント間の完全分離
- 会話間の完全分離
- 他の会話のファイルにはアクセス不可

---

## AIへの指示

ワークスペースが有効な場合、AIには以下の情報がシステムプロンプトに追加されます：

- 現在のワークスペースパス
- 利用可能なファイル一覧（パス、サイズ、ソース）
- ファイル操作のガイドライン
- セキュリティ制限の説明

AIはファイルの内容を読まなくても、どのファイルが利用可能かを把握できます。

---

## API一覧

| メソッド | パス | 説明 |
|---------|------|------|
| POST | /tenants/{tenant_id}/conversations | 会話作成（`workspace_enabled: true`で有効化） |
| POST | /tenants/{tenant_id}/conversations/{conversation_id}/stream | ストリーミング実行（ファイル添付可） |
| GET | /tenants/{tenant_id}/conversations/{conversation_id}/files | ファイル一覧 |
| GET | /tenants/{tenant_id}/conversations/{conversation_id}/files/download?path=xxx | ファイルダウンロード |
| GET | /tenants/{tenant_id}/conversations/{conversation_id}/files/presented | AIが作成したファイル一覧 |

---

## エラーハンドリング

| HTTPステータス | 説明 |
|---------------|------|
| 400 Bad Request | ワークスペースが無効な会話でファイル操作を試行 |
| 403 Forbidden | アクセス権限なし |
| 404 Not Found | ファイルが見つからない |
| 500 Internal Server Error | サーバーエラー（S3接続エラーなど） |
