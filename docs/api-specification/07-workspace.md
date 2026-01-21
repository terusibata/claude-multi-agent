# ワークスペースAPI

会話専用ワークスペースのファイル管理を行うAPIです。
S3をバックエンドとしてファイルの保存・取得を提供します。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/tenants/{tenant_id}/conversations/{conversation_id}/files` |
| 認証 | 必要 |
| スコープ | 会話単位 |

### ワークスペースとは

ワークスペースは、各会話に紐づくファイル保存領域です：

- 会話作成時に`workspace_enabled: true`で有効化
- ユーザーがアップロードしたファイルを保存
- AIが作成・編集したファイルを保存
- S3をバックエンドとして利用

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `.../files` | ファイル一覧取得 |
| GET | `.../files/download` | ファイルダウンロード |
| GET | `.../files/presented` | AIが作成したファイル一覧 |

**注意**: ファイルのアップロードは[ストリーミングAPI](./04-streaming.md)の`files`パラメータで行います。

---

## データ型

### ConversationFileInfo

```typescript
interface ConversationFileInfo {
  file_id: string;                          // ファイルID
  file_path: string;                        // ワークスペース内のファイルパス
  original_name: string;                    // 元のファイル名
  file_size: number;                        // ファイルサイズ（バイト）
  mime_type: string | null;                 // MIMEタイプ
  version: number;                          // バージョン番号
  source: "user_upload" | "ai_created" | "ai_modified";  // ソース
  is_presented: boolean;                    // Presentedファイルフラグ
  checksum: string | null;                  // SHA256チェックサム
  description: string | null;               // ファイル説明
  created_at: string;                       // 作成日時
  updated_at: string;                       // 更新日時
}
```

### ファイルソースの説明

| ソース | 説明 |
|--------|------|
| `user_upload` | ユーザーがアップロードしたファイル |
| `ai_created` | AIが新規作成したファイル |
| `ai_modified` | AIが既存ファイルを編集したもの |

### Presentedファイルとは

AIがユーザーに「提示」したファイルです。
例えば、分析結果のCSVやレポートのMarkdownなど、
AIがユーザーにダウンロードしてほしいとマークしたファイルです。

---

## GET .../files

会話のファイル一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID |

### レスポンス

**成功時 (200 OK)**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "files": [
    {
      "file_id": "file-001",
      "file_path": "uploads/data.csv",
      "original_name": "data.csv",
      "file_size": 10240,
      "mime_type": "text/csv",
      "version": 1,
      "source": "user_upload",
      "is_presented": false,
      "checksum": "abc123...",
      "description": null,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:00Z"
    },
    {
      "file_id": "file-002",
      "file_path": "outputs/analysis_result.csv",
      "original_name": "analysis_result.csv",
      "file_size": 5120,
      "mime_type": "text/csv",
      "version": 1,
      "source": "ai_created",
      "is_presented": true,
      "checksum": "def456...",
      "description": "データ分析の結果",
      "created_at": "2024-01-15T10:35:00Z",
      "updated_at": "2024-01-15T10:35:00Z"
    }
  ],
  "total_count": 2,
  "total_size": 15360
}
```

**エラー: アクセス拒否 (403 Forbidden)**

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "このワークスペースへのアクセス権がありません"
  }
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000/files" \
  -H "X-API-Key: your_api_key"
```

---

## GET .../files/download

ファイルをダウンロードします。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID |

### クエリパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `path` | string | Yes | ファイルパス（例: `uploads/data.csv`） |

### レスポンス

**成功時 (200 OK)**

- **Content-Type**: ファイルのMIMEタイプ
- **Content-Disposition**: `attachment; filename*=UTF-8''<encoded_filename>`
- **Body**: ファイルの内容（バイナリ）

**エラー: ファイル不存在 (404 Not Found)**

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "ファイルが見つかりません"
  }
}
```

**エラー: アクセス拒否 (403 Forbidden)**

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "このファイルへのアクセス権がありません"
  }
}
```

### curlの例

```bash
# ファイルをダウンロード
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000/files/download?path=uploads/data.csv" \
  -H "X-API-Key: your_api_key" \
  -o downloaded_data.csv

# AIが作成したファイルをダウンロード
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000/files/download?path=outputs/analysis_result.csv" \
  -H "X-API-Key: your_api_key" \
  -o analysis_result.csv
```

---

## GET .../files/presented

AIがユーザーに提示したファイル（Presentedファイル）の一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID |

### レスポンス

**成功時 (200 OK)**

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "files": [
    {
      "file_id": "file-002",
      "file_path": "outputs/analysis_result.csv",
      "original_name": "analysis_result.csv",
      "file_size": 5120,
      "mime_type": "text/csv",
      "version": 1,
      "source": "ai_created",
      "is_presented": true,
      "checksum": "def456...",
      "description": "データ分析の結果",
      "created_at": "2024-01-15T10:35:00Z",
      "updated_at": "2024-01-15T10:35:00Z"
    },
    {
      "file_id": "file-003",
      "file_path": "outputs/report.md",
      "original_name": "report.md",
      "file_size": 2048,
      "mime_type": "text/markdown",
      "version": 1,
      "source": "ai_created",
      "is_presented": true,
      "checksum": "ghi789...",
      "description": "分析レポート",
      "created_at": "2024-01-15T10:40:00Z",
      "updated_at": "2024-01-15T10:40:00Z"
    }
  ]
}
```

### フロントエンドでの使用例

1. ストリーミング完了後、このAPIでPresentedファイルを取得
2. ユーザーにダウンロードボタンを表示
3. `download`エンドポイントでファイルをダウンロード

```typescript
// 例: Presented ファイルの取得とダウンロードリンク生成
async function getPresentedFiles(tenantId: string, conversationId: string) {
  const response = await fetch(
    `/api/tenants/${tenantId}/conversations/${conversationId}/files/presented`,
    { headers: { 'X-API-Key': apiKey } }
  );
  const data = await response.json();

  // ダウンロードURLを生成
  return data.files.map(file => ({
    ...file,
    downloadUrl: `/api/tenants/${tenantId}/conversations/${conversationId}/files/download?path=${encodeURIComponent(file.file_path)}`
  }));
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000/files/presented" \
  -H "X-API-Key: your_api_key"
```

---

## ファイルパスの構造

ワークスペース内のファイルは以下の構造で保存されます：

```
workspace/
├── uploads/           # ユーザーアップロードファイル
│   ├── data.csv
│   └── image.png
└── outputs/           # AI作成ファイル
    ├── result.csv
    └── report.md
```

| ディレクトリ | 説明 |
|-------------|------|
| `uploads/` | ユーザーがアップロードしたファイル |
| `outputs/` | AIが作成したファイル |

---

## ファイルアップロードについて

ファイルのアップロードは、[ストリーミングAPI](./04-streaming.md)の`files`パラメータで行います：

```bash
curl -X POST ".../stream" \
  -H "X-API-Key: your_api_key" \
  -F 'request_data={"user_input": "このファイルを分析して", "executor": {...}}' \
  -F 'files=@data.csv' \
  -F 'files=@image.png'
```

アップロードされたファイルは自動的に`uploads/`ディレクトリに保存されます。

---

## 関連API

- [会話管理API](./03-conversations.md) - 会話の`workspace_enabled`設定
- [ストリーミングAPI](./04-streaming.md) - ファイルアップロード
