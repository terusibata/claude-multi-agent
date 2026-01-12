# セッション専用ワークスペース機能

## 概要

セッション専用ワークスペースは、AIエージェントがファイル操作を行う際に、セッションごとに独立した作業ディレクトリを提供する機能です。これにより、異なるセッション間でのファイルの競合を防ぎ、セキュアなファイル管理を実現します。

### ユースケース

| ユースケース | ワークスペース | 説明 |
|-------------|--------------|------|
| ドキュメント分析・要約 | **必要** | ユーザーがPDFやExcelをアップロードし、AIが分析結果をファイル出力 |
| コード生成・レビュー | **必要** | AIがコードファイルを生成し、ユーザーがダウンロード |
| データ変換 | **必要** | CSV→JSON変換など、ファイル入出力が必要なタスク |
| 翻訳・校正 | 不要 | テキストの入出力のみで完結 |
| 質問応答 | 不要 | 会話のみで完結 |
| 情報検索 | 不要 | MCPサーバー経由でデータ取得のみ |

---

## 設定方法

### 1. エージェント設定での有効化

エージェント設定（AgentConfig）でワークスペースを有効化できます：

```json
PUT /api/tenants/{tenant_id}/agent-configs/{config_id}
{
  "name": "ドキュメント分析エージェント",
  "system_prompt": "...",
  "allowed_tools": ["Read", "Write", "Bash", "Glob"],
  "workspace_enabled": true,  // ← エージェント設定で有効化
  "workspace_auto_cleanup_days": 30  // ← 自動クリーンアップ日数
}
```

### 2. 実行時の有効化

エージェント設定で無効でも、実行時に有効化できます：

```json
POST /api/tenants/{tenant_id}/execute
{
  "agent_config_id": "...",
  "model_id": "...",
  "user_input": "このファイルを分析してください",
  "executor": {...},
  "enable_workspace": true  // ← 実行時に有効化（オプション）
}
```

> **優先順位**: `enable_workspace`（実行時） > `workspace_enabled`（エージェント設定）

---

## ファイルアップロード

### エンドポイント

```
POST /api/tenants/{tenant_id}/sessions/{session_id}/upload-files
```

### リクエスト形式

**Content-Type**: `multipart/form-data`

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `files` | File[] | ○ | アップロードするファイル（複数可） |
| `target_dir` | string | - | 保存先ディレクトリ（デフォルト: `uploads`） |

### cURLの例

```bash
# 単一ファイルのアップロード
curl -X POST \
  "http://localhost:8000/api/tenants/tenant-1/sessions/session-123/upload-files" \
  -F "files=@/path/to/document.pdf" \
  -F "target_dir=uploads"

# 複数ファイルのアップロード
curl -X POST \
  "http://localhost:8000/api/tenants/tenant-1/sessions/session-123/upload-files" \
  -F "files=@/path/to/file1.csv" \
  -F "files=@/path/to/file2.xlsx" \
  -F "target_dir=data"
```

### JavaScriptの例

```javascript
const formData = new FormData();
formData.append('files', file1);
formData.append('files', file2);
formData.append('target_dir', 'uploads');

const response = await fetch(
  `/api/tenants/${tenantId}/sessions/${sessionId}/upload-files`,
  {
    method: 'POST',
    body: formData,
  }
);

const result = await response.json();
console.log(result.uploaded_files);
```

### レスポンス例

```json
{
  "success": true,
  "uploaded_files": [
    {
      "file_id": "a1b2c3d4-...",
      "file_path": "uploads/document.pdf",
      "original_name": "document.pdf",
      "file_size": 1048576,
      "mime_type": "application/pdf",
      "version": 1,
      "source": "user_upload",
      "is_presented": false,
      "checksum": "abc123...",
      "created_at": "2025-01-10T12:00:00Z",
      "updated_at": "2025-01-10T12:00:00Z"
    }
  ],
  "failed_files": [],
  "message": "1ファイルをアップロードしました"
}
```

---

## ファイル一覧取得

### エンドポイント

```
GET /api/tenants/{tenant_id}/sessions/{session_id}/list-files
```

### クエリパラメータ

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|----------|------|
| `include_all_versions` | boolean | false | 全バージョンを含めるか |

### レスポンス例

```json
{
  "chat_session_id": "session-123",
  "files": [
    {
      "file_id": "a1b2c3d4-...",
      "file_path": "uploads/data.csv",
      "original_name": "data.csv",
      "file_size": 2048,
      "version": 2,
      "source": "user_upload",
      "is_presented": false
    },
    {
      "file_id": "e5f6g7h8-...",
      "file_path": "outputs/analysis_result.json",
      "original_name": "analysis_result.json",
      "file_size": 512,
      "version": 1,
      "source": "ai_created",
      "is_presented": true
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
GET /api/tenants/{tenant_id}/sessions/{session_id}/download-file
```

### クエリパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `path` | string | ○ | ファイルパス（ワークスペース内） |
| `version` | integer | - | バージョン番号（省略時は最新） |

### 使用例

```bash
# 最新バージョンをダウンロード
curl -O "http://localhost:8000/api/tenants/tenant-1/sessions/session-123/download-file?path=outputs/result.json"

# 特定バージョンをダウンロード
curl -O "http://localhost:8000/api/tenants/tenant-1/sessions/session-123/download-file?path=uploads/data.csv&version=1"
```

---

## Presentedファイル

AIが作成したファイルのうち、ユーザーに提示したいものを「Presented」としてマークできます。

### Presentedファイル一覧取得

```
GET /api/tenants/{tenant_id}/sessions/{session_id}/presented-files
```

### ファイルをPresentedとしてマーク

```
POST /api/tenants/{tenant_id}/sessions/{session_id}/present-file
```

```json
{
  "file_path": "outputs/analysis_result.xlsx",
  "description": "売上データの分析結果"
}
```

---

## バージョン管理

同じパスにファイルをアップロードすると、自動的にバージョンがインクリメントされます。

```
uploads/data.csv (version 1) → 初回アップロード
uploads/data.csv (version 2) → 再アップロード
uploads/data.csv (version 3) → 再アップロード
```

- デフォルトでは最新バージョンのみ表示
- `include_all_versions=true` で全バージョン取得可能
- 古いバージョンも `version` パラメータで個別にダウンロード可能

---

## ワークスペースのクリーンアップ

### 自動クリーンアップ

エージェント設定で `workspace_auto_cleanup_days` を設定すると、指定日数経過後にアーカイブ済みセッションのワークスペースが自動削除されます。

### 手動クリーンアップ

```
POST /api/tenants/{tenant_id}/workspace/cleanup
```

```json
{
  "older_than_days": 30,
  "dry_run": true  // trueの場合、削除せずにプレビューのみ
}
```

---

## ディレクトリ構造

```
/skills/
└── tenant_{tenant_id}/
    └── workspaces/
        └── {chat_session_id}/
            ├── uploads/      # ユーザーアップロードファイル
            ├── outputs/      # AI生成ファイル
            └── temp/         # 一時ファイル
```

---

## セキュリティ

### パストラバーサル防止

- すべてのパスは正規化後に検証
- ワークスペース外へのアクセスは完全にブロック
- `..` や絶対パスは拒否

### アイソレーション

- テナント間の完全分離
- セッション間の完全分離
- 他のセッションのファイルにはアクセス不可

### ファイルサイズ制限

| 制限 | 値 |
|------|-----|
| 1ファイルあたり | 50MB |
| 1セッションあたり | 500MB |

---

## AIへの指示

ワークスペースが有効な場合、AIには以下の情報がシステムプロンプトに追加されます：

- 現在のワークスペースパス
- 利用可能なファイル一覧（パス、サイズ、ソース）
- ファイル操作のガイドライン
- セキュリティ制限の説明

AIはファイルの内容を読まなくても、どのファイルが利用可能かを把握できます。

---

## エラーハンドリング

| HTTPステータス | 説明 |
|---------------|------|
| 403 Forbidden | パストラバーサル検出、アクセス権限なし |
| 404 Not Found | ファイルが見つからない |
| 413 Payload Too Large | ファイルサイズ超過 |
| 500 Internal Server Error | サーバーエラー |
