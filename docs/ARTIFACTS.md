# アーティファクト機能の実装ガイド

## 概要

アーティファクト機能は、Claude Multi-Agentシステムでエージェントが生成したファイル（コード、ドキュメント、画像など）をフロントエンドで表示・管理するための機能です。

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│ 1. リアルタイム表示（SSEイベント）                              │
│    - 新イベント: artifact_created                              │
│    - ファイル作成時に即座にフロントエンドに通知                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. 永続化（Artifactsテーブル + ストレージ）                     │
│    - DBにメタデータを保存                                      │
│    - ローカルファイル or S3に実ファイルを保存                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. ストレージ抽象化（開発/本番切り替え）                        │
│    - LocalStorage: 開発環境用                                  │
│    - S3Storage: 本番環境用                                     │
└─────────────────────────────────────────────────────────────┘
```

## データモデル

### Artifactテーブル

```python
class Artifact:
    artifact_id: str              # UUID
    chat_session_id: str          # セッションID
    turn_number: int              # ターン番号
    tool_execution_log_id: str    # ツール実行ログID（オプション）
    artifact_type: str            # file / code / notebook / image / document
    filename: str                 # ファイル名
    file_path: str                # ローカルファイルパス
    s3_key: str                   # S3キー（S3使用時）
    content: str                  # ファイル内容（10KB以下のみDB保存）
    mime_type: str                # MIMEタイプ
    file_size: int                # ファイルサイズ（バイト）
    tool_name: str                # Write / NotebookEdit
    title: str                    # タイトル
    description: str              # 説明
    created_at: datetime          # 作成日時
```

### ファイルシステム構造

```
/artifacts/
├── tenant_xxx/
│   ├── session-id-1/
│   │   ├── hello.py
│   │   ├── data.json
│   │   └── notebook.ipynb
│   └── session-id-2/
│       └── report.md
└── tenant_yyy/
    └── ...
```

## SSEイベント: artifact_created

Writeツール・NotebookEditツールが実行されると、`artifact_created`イベントがストリーミングで送信されます。

### イベント形式

```typescript
{
  event: "artifact_created",
  data: {
    artifact_id: string,           // UUID
    artifact_type: string,          // "file" | "code" | "notebook" | "image" | "document"
    filename: string,               // ファイル名
    file_path: string | null,       // ローカルファイルパス
    s3_key: string | null,          // S3キー
    content: string | null,         // ファイル内容（10KB以下のみ）
    mime_type: string | null,       // MIMEタイプ
    file_size: number,              // ファイルサイズ（バイト）
    title: string | null,           // タイトル
    description: string | null      // 説明
  }
}
```

## API エンドポイント

### 1. セッションのアーティファクト一覧取得

```
GET /api/tenants/{tenant_id}/sessions/{session_id}/artifacts
```

**クエリパラメータ:**
- `limit`: 取得件数（デフォルト: 50、最大: 200）
- `offset`: オフセット（デフォルト: 0）
- `artifact_type`: フィルタリング用アーティファクトタイプ（オプション）

**レスポンス:**
```json
{
  "artifacts": [
    {
      "artifact_id": "123e4567-e89b-12d3-a456-426614174000",
      "chat_session_id": "session-uuid",
      "turn_number": 1,
      "artifact_type": "code",
      "filename": "hello.py",
      "file_path": "/artifacts/tenant_xxx/session-id/hello.py",
      "s3_key": null,
      "content": "print('Hello, World!')",
      "mime_type": "text/x-python",
      "file_size": 22,
      "tool_name": "Write",
      "title": "hello.py",
      "description": "Writeツールで作成されたファイル",
      "created_at": "2025-01-10T10:00:00Z"
    }
  ],
  "total_count": 1
}
```

### 2. アーティファクト詳細取得

```
GET /api/tenants/{tenant_id}/artifacts/{artifact_id}?include_content=true
```

**クエリパラメータ:**
- `include_content`: ファイル内容を含めるかどうか（デフォルト: false）

**レスポンス:**
```json
{
  "artifact_id": "123e4567-e89b-12d3-a456-426614174000",
  "chat_session_id": "session-uuid",
  "turn_number": 1,
  "artifact_type": "code",
  "filename": "hello.py",
  "file_path": "/artifacts/tenant_xxx/session-id/hello.py",
  "s3_key": null,
  "content": "print('Hello, World!')",
  "mime_type": "text/x-python",
  "file_size": 22,
  "tool_name": "Write",
  "title": "hello.py",
  "description": "Writeツールで作成されたファイル",
  "created_at": "2025-01-10T10:00:00Z"
}
```

### 3. アーティファクト削除

```
DELETE /api/tenants/{tenant_id}/artifacts/{artifact_id}
```

**レスポンス:** 204 No Content

## フロントエンド実装例

### 1. SSEイベントのリスニング

```typescript
const eventSource = new EventSource('/api/tenants/tenant-xxx/execute');

eventSource.addEventListener('artifact_created', (event) => {
  const artifact = JSON.parse(event.data);

  console.log('New artifact created:', artifact.filename);

  // アーティファクトを表示
  displayArtifact(artifact);
});

function displayArtifact(artifact: ArtifactData) {
  const container = document.getElementById('artifacts-container');

  // アーティファクトタイプに応じて表示を変える
  switch (artifact.artifact_type) {
    case 'code':
      // コードエディタで表示
      renderCodeArtifact(artifact);
      break;
    case 'image':
      // 画像として表示
      renderImageArtifact(artifact);
      break;
    case 'notebook':
      // Jupyterノートブックとして表示
      renderNotebookArtifact(artifact);
      break;
    default:
      // テキストエディタで表示
      renderTextArtifact(artifact);
  }
}
```

### 2. コードアーティファクトの表示（Monaco Editor使用例）

```typescript
function renderCodeArtifact(artifact: ArtifactData) {
  // Monaco Editorで表示
  const editor = monaco.editor.create(document.getElementById('editor'), {
    value: artifact.content || '',
    language: getLanguageFromMimeType(artifact.mime_type),
    theme: 'vs-dark',
    readOnly: true,
  });

  // ダウンロードボタン
  const downloadBtn = document.createElement('button');
  downloadBtn.textContent = 'Download';
  downloadBtn.onclick = () => {
    downloadArtifact(artifact.artifact_id, artifact.filename);
  };
}

function getLanguageFromMimeType(mimeType: string | null): string {
  const languageMap: Record<string, string> = {
    'text/x-python': 'python',
    'text/javascript': 'javascript',
    'application/json': 'json',
    'text/html': 'html',
    'text/css': 'css',
  };
  return languageMap[mimeType || ''] || 'plaintext';
}
```

### 3. アーティファクト一覧の取得

```typescript
async function loadArtifacts(sessionId: string) {
  const response = await fetch(
    `/api/tenants/tenant-xxx/sessions/${sessionId}/artifacts?limit=50`
  );

  const data = await response.json();

  data.artifacts.forEach((artifact: ArtifactData) => {
    displayArtifact(artifact);
  });

  console.log(`Loaded ${data.total_count} artifacts`);
}
```

### 4. アーティファクトのダウンロード

```typescript
async function downloadArtifact(artifactId: string, filename: string) {
  // include_content=trueで内容を取得
  const response = await fetch(
    `/api/tenants/tenant-xxx/artifacts/${artifactId}?include_content=true`
  );

  const artifact = await response.json();

  // Blobとしてダウンロード
  const blob = new Blob([artifact.content], {
    type: artifact.mime_type || 'text/plain'
  });

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
```

### 5. Reactコンポーネント例

```tsx
import React, { useEffect, useState } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface Artifact {
  artifact_id: string;
  filename: string;
  artifact_type: string;
  content: string | null;
  mime_type: string | null;
  file_size: number;
  created_at: string;
}

export const ArtifactDisplay: React.FC<{ artifact: Artifact }> = ({ artifact }) => {
  const [content, setContent] = useState<string>(artifact.content || '');

  useEffect(() => {
    // contentがない場合はAPIから取得
    if (!artifact.content) {
      fetchArtifactContent(artifact.artifact_id).then(setContent);
    }
  }, [artifact.artifact_id]);

  const language = getLanguageFromMimeType(artifact.mime_type);

  return (
    <div className="artifact-container">
      <div className="artifact-header">
        <h3>{artifact.filename}</h3>
        <span className="artifact-type">{artifact.artifact_type}</span>
        <button onClick={() => downloadArtifact(artifact.artifact_id, artifact.filename)}>
          Download
        </button>
      </div>

      <div className="artifact-content">
        {artifact.artifact_type === 'code' ? (
          <SyntaxHighlighter language={language} style={vscDarkPlus}>
            {content}
          </SyntaxHighlighter>
        ) : (
          <pre>{content}</pre>
        )}
      </div>

      <div className="artifact-footer">
        <span>Size: {formatFileSize(artifact.file_size)}</span>
        <span>Created: {new Date(artifact.created_at).toLocaleString()}</span>
      </div>
    </div>
  );
};

async function fetchArtifactContent(artifactId: string): Promise<string> {
  const response = await fetch(
    `/api/tenants/tenant-xxx/artifacts/${artifactId}?include_content=true`
  );
  const data = await response.json();
  return data.content || '';
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getLanguageFromMimeType(mimeType: string | null): string {
  const languageMap: Record<string, string> = {
    'text/x-python': 'python',
    'text/javascript': 'javascript',
    'application/json': 'json',
    'text/html': 'html',
    'text/css': 'css',
  };
  return languageMap[mimeType || ''] || 'text';
}

function downloadArtifact(artifactId: string, filename: string) {
  // ダウンロード処理（前述の例を参照）
}
```

## 環境設定

### 開発環境（ローカルストレージ）

`.env` ファイル:
```bash
# アーティファクトストレージ設定
ARTIFACTS_STORAGE_TYPE=local
ARTIFACTS_BASE_PATH=/artifacts
```

### 本番環境（S3ストレージ）

`.env` ファイル:
```bash
# アーティファクトストレージ設定
ARTIFACTS_STORAGE_TYPE=s3
ARTIFACTS_S3_BUCKET=my-artifacts-bucket
ARTIFACTS_S3_PREFIX=artifacts/
AWS_REGION=us-west-2
```

## マイグレーション実行

```bash
# Dockerコンテナ内でマイグレーション実行
docker exec -it claude-multi-agent bash
alembic upgrade head
```

## トラブルシューティング

### 1. アーティファクトが作成されない

- Writeツール・NotebookEditツールが正しく実行されているか確認
- ログを確認: `docker logs claude-multi-agent`
- `/artifacts` ディレクトリのパーミッションを確認

### 2. ファイル内容が取得できない

- `include_content=true` クエリパラメータを指定しているか確認
- ファイルサイズが大きい場合、DBに保存されていない可能性がある
- ストレージサービスが正しく設定されているか確認

### 3. SSEイベントが届かない

- EventSourceが正しく接続されているか確認
- CORSの設定を確認
- ネットワークプロキシがSSEをブロックしていないか確認

## ベストプラクティス

1. **小さいファイルのみプレビュー**: 10KB以下のファイルのみ `content` フィールドに含める
2. **遅延ロード**: 大きいファイルはユーザーが明示的に要求したときのみ取得
3. **キャッシング**: フロントエンドでアーティファクト内容をキャッシュして再取得を避ける
4. **エラーハンドリング**: ファイル取得失敗時に適切なフォールバックを提供
5. **セキュリティ**: テナントIDベースのアクセス制御を常に実施

## 次のステップ

- アーティファクトの編集機能の追加
- バージョン管理機能の追加
- アーティファクト間の関連付け（依存関係）管理
- フルテキスト検索機能の追加
