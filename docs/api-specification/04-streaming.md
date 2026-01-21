# ストリーミングAPI (Server-Sent Events)

AIエージェント実行のリアルタイムストリーミングを提供するAPIです。
**これはフロントエンド実装において最も重要なAPIです。**

## 概要

| 項目 | 値 |
|------|-----|
| エンドポイント | `POST /api/tenants/{tenant_id}/conversations/{conversation_id}/stream` |
| 認証 | 必要 |
| Content-Type | `multipart/form-data` |
| レスポンス形式 | `text/event-stream` (Server-Sent Events) |

---

## リクエスト

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `conversation_id` | string | Yes | 会話ID（UUID） |

### リクエストボディ（multipart/form-data）

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `request_data` | string (JSON) | Yes | StreamRequestのJSON文字列 |
| `files` | File[] | No | 添付ファイル（複数可） |

### StreamRequest JSON構造

```typescript
interface StreamRequest {
  user_input: string;                    // ユーザー入力（必須）
  executor: ExecutorInfo;                // 実行者情報（必須）
  tokens?: Record<string, string>;       // MCP認証トークン（オプション）
  preferred_skills?: string[];           // 優先使用するSkill名（オプション）
}

interface ExecutorInfo {
  user_id: string;      // ユーザーID（必須）
  name: string;         // 名前（必須）
  email: string;        // メールアドレス（必須）
  employee_id?: string; // 社員番号（オプション）
}
```

### リクエスト例

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/conversations/550e8400-e29b-41d4-a716-446655440000/stream" \
  -H "X-API-Key: your_api_key" \
  -F 'request_data={
    "user_input": "このCSVファイルを分析してください",
    "executor": {
      "user_id": "user-001",
      "name": "田中太郎",
      "email": "tanaka@example.com"
    },
    "preferred_skills": ["data-analysis"]
  }' \
  -F 'files=@data.csv'
```

---

## SSEイベント形式

### 共通構造

すべてのイベントは以下の形式で送信されます：

```
event: <event_type>
data: {"seq": <number>, "timestamp": "<ISO8601>", ...}

```

### イベントデータの共通フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `seq` | number | シーケンス番号（順序保証用） |
| `timestamp` | string | イベント発生時刻（ISO 8601） |

---

## イベントタイプ一覧

| イベント | 説明 | 発生タイミング |
|---------|------|---------------|
| `init` | セッション初期化 | 実行開始時 |
| `thinking` | Extended Thinking | AI思考プロセス中 |
| `assistant` | アシスタントメッセージ | AI応答時 |
| `tool_call` | ツール呼び出し開始 | ツール実行開始時 |
| `tool_result` | ツール実行結果 | ツール実行完了時 |
| `subagent_start` | サブエージェント開始 | サブエージェント起動時 |
| `subagent_end` | サブエージェント終了 | サブエージェント完了時 |
| `progress` | 進捗更新 | 処理状態変更時 |
| `title` | タイトル生成 | タイトル自動生成時 |
| `ping` | ハートビート | 10秒ごと |
| `done` | 完了 | 実行完了時 |
| `error` | エラー | エラー発生時 |

---

## 各イベントの詳細

### 1. init（セッション初期化）

セッション開始時に1回だけ送信されます。

```typescript
interface InitEvent {
  seq: number;
  timestamp: string;
  session_id: string;           // セッションID
  tools: string[];              // 利用可能なツールリスト
  model: string;                // 使用モデル
  conversation_id?: string;     // 会話ID
}
```

**例:**
```json
{
  "seq": 1,
  "timestamp": "2024-01-15T10:30:00.123Z",
  "session_id": "sess_abc123def456",
  "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "mcp__servicenow__search"],
  "model": "claude-sonnet-4",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 2. thinking（Extended Thinking）

AIの思考プロセスを表示します。

```typescript
interface ThinkingEvent {
  seq: number;
  timestamp: string;
  content: string;               // 思考内容
  parent_agent_id?: string;      // 親エージェントID（サブエージェント内の場合）
}
```

**例:**
```json
{
  "seq": 2,
  "timestamp": "2024-01-15T10:30:01.456Z",
  "content": "ユーザーはCSVファイルの分析を依頼しています。まずファイルの内容を確認し、データの構造を理解する必要があります..."
}
```

### 3. assistant（アシスタントメッセージ）

AIからのテキスト応答やツール使用を含みます。

```typescript
interface AssistantEvent {
  seq: number;
  timestamp: string;
  content_blocks: ContentBlock[];    // コンテンツブロックのリスト
  parent_agent_id?: string;          // 親エージェントID
}

type ContentBlock = TextBlock | ToolUseBlock;

interface TextBlock {
  type: "text";
  text: string;
}

interface ToolUseBlock {
  type: "tool_use";
  id: string;          // ツール使用ID
  name: string;        // ツール名
  input: object;       // ツール入力
}
```

**例:**
```json
{
  "seq": 3,
  "timestamp": "2024-01-15T10:30:02.789Z",
  "content_blocks": [
    {
      "type": "text",
      "text": "CSVファイルを分析します。まずファイルの内容を確認させてください。"
    },
    {
      "type": "tool_use",
      "id": "tu_abc123",
      "name": "Read",
      "input": {
        "file_path": "/workspace/data.csv"
      }
    }
  ]
}
```

### 4. tool_call（ツール呼び出し開始）

ツール実行の開始を通知します。

```typescript
interface ToolCallEvent {
  seq: number;
  timestamp: string;
  tool_use_id: string;           // ツール使用ID
  tool_name: string;             // ツール名
  input: object;                 // 入力パラメータ（500文字で切り詰め）
  summary: string;               // サマリー
  parent_agent_id?: string;      // 親エージェントID
}
```

**例:**
```json
{
  "seq": 4,
  "timestamp": "2024-01-15T10:30:03.012Z",
  "tool_use_id": "tu_abc123",
  "tool_name": "Read",
  "input": {
    "file_path": "/workspace/data.csv"
  },
  "summary": "ファイルを読み取ります"
}
```

### 5. tool_result（ツール実行結果）

ツール実行の結果を通知します。

```typescript
interface ToolResultEvent {
  seq: number;
  timestamp: string;
  tool_use_id: string;           // ツール使用ID
  tool_name: string;             // ツール名
  status: "completed" | "error"; // ステータス
  content: string;               // 結果内容（プレビュー）
  is_error: boolean;             // エラーかどうか
  parent_agent_id?: string;      // 親エージェントID
}
```

**例:**
```json
{
  "seq": 5,
  "timestamp": "2024-01-15T10:30:03.345Z",
  "tool_use_id": "tu_abc123",
  "tool_name": "Read",
  "status": "completed",
  "content": "id,name,value\n1,Alice,100\n2,Bob,200\n...",
  "is_error": false
}
```

#### 組み込みツール: present_files（ファイル提示）

AIが作成・編集したファイルをユーザーに提示する組み込みツールです。
Write/Edit/NotebookEditでファイルを作成・編集した後、AIはこのツールを使用してユーザーにファイルを提示します。

**tool_call イベント:**
```json
{
  "seq": 20,
  "timestamp": "2024-01-15T10:30:15.123Z",
  "tool_use_id": "tu_present_001",
  "tool_name": "mcp__file-presentation__present_files",
  "input": {
    "file_paths": ["analysis_result.csv", "report.md"],
    "description": "データ分析結果のCSVファイルとレポート"
  },
  "summary": "ファイルをユーザーに提示します"
}
```

**tool_result イベント（成功時）:**
```json
{
  "seq": 21,
  "timestamp": "2024-01-15T10:30:15.456Z",
  "tool_use_id": "tu_present_001",
  "tool_name": "mcp__file-presentation__present_files",
  "status": "completed",
  "content": "ファイルを提示しました: データ分析結果のCSVファイルとレポート\n\n【提示されたファイル】\n• analysis_result.csv (5120 bytes)\n  ダウンロードパス: analysis_result.csv\n• report.md (2048 bytes)\n  ダウンロードパス: report.md",
  "is_error": false
}
```

**tool_result イベント（一部ファイルが見つからない場合）:**
```json
{
  "seq": 21,
  "timestamp": "2024-01-15T10:30:15.456Z",
  "tool_use_id": "tu_present_001",
  "tool_name": "mcp__file-presentation__present_files",
  "status": "completed",
  "content": "ファイルを提示しました: 分析レポート\n\n【提示されたファイル】\n• report.md (2048 bytes)\n  ダウンロードパス: report.md\n\n【見つからなかったファイル】\n• missing.csv",
  "is_error": false
}
```

**フロントエンドでの処理:**

`mcp__file-presentation__present_files` のtool_resultを受信した場合、フロントエンドは以下の処理を行うことを推奨します：

1. ダウンロードパスを抽出してダウンロードボタンを表示
2. [ワークスペースAPI](./07-workspace.md)の`GET .../files/download`でファイルをダウンロード

```typescript
// tool_resultイベントの処理例
function handleToolResult(event: ToolResultEvent) {
  if (event.tool_name === "mcp__file-presentation__present_files") {
    // ダウンロードパスをパースしてUIに表示
    const downloadPaths = parseDownloadPaths(event.content);
    showDownloadButtons(downloadPaths);
  }
}
```

### 6. subagent_start（サブエージェント開始）

サブエージェント（Taskツール）の開始を通知します。

```typescript
interface SubagentStartEvent {
  seq: number;
  timestamp: string;
  agent_id: string;              // エージェントID（tool_use_id）
  agent_type: string;            // エージェントタイプ
  description: string;           // 説明
  model?: string;                // 使用モデル
}
```

**例:**
```json
{
  "seq": 10,
  "timestamp": "2024-01-15T10:30:10.123Z",
  "agent_id": "tu_subagent_001",
  "agent_type": "Explore",
  "description": "コードベースを探索",
  "model": "claude-haiku-3"
}
```

### 7. subagent_end（サブエージェント終了）

サブエージェントの終了を通知します。

```typescript
interface SubagentEndEvent {
  seq: number;
  timestamp: string;
  agent_id: string;                       // エージェントID
  agent_type: string;                     // エージェントタイプ
  status: "completed" | "error";          // ステータス
  result_preview?: string;                // 結果プレビュー
}
```

**例:**
```json
{
  "seq": 15,
  "timestamp": "2024-01-15T10:30:25.456Z",
  "agent_id": "tu_subagent_001",
  "agent_type": "Explore",
  "status": "completed",
  "result_preview": "5件のファイルが見つかりました..."
}
```

### 8. progress（進捗更新）

処理の進捗状況を通知します。

```typescript
interface ProgressEvent {
  seq: number;
  timestamp: string;
  type: "thinking" | "generating" | "tool";  // 進捗タイプ
  message: string;                           // 進捗メッセージ
  tool_use_id?: string;                      // ツール使用ID（tool タイプ時）
  tool_name?: string;                        // ツール名
  tool_status?: "pending" | "running" | "completed" | "error";
  parent_agent_id?: string;                  // 親エージェントID
}
```

**例:**
```json
{
  "seq": 6,
  "timestamp": "2024-01-15T10:30:04.567Z",
  "type": "tool",
  "message": "ファイルを読み込み中...",
  "tool_use_id": "tu_abc123",
  "tool_name": "Read",
  "tool_status": "running"
}
```

### 9. title（タイトル生成）

会話タイトルが自動生成されたときに送信されます。

```typescript
interface TitleEvent {
  seq: number;
  timestamp: string;
  title: string;                 // 生成されたタイトル
}
```

**例:**
```json
{
  "seq": 20,
  "timestamp": "2024-01-15T10:31:00.789Z",
  "title": "CSVデータ分析と可視化"
}
```

### 10. ping（ハートビート）

接続維持のために10秒ごとに送信されます。

```typescript
interface PingEvent {
  seq: number;
  timestamp: string;
  elapsed_ms: number;            // 実行開始からの経過時間（ミリ秒）
}
```

**例:**
```json
{
  "seq": 7,
  "timestamp": "2024-01-15T10:30:10.000Z",
  "elapsed_ms": 10000
}
```

### 11. done（完了）

実行完了時に送信されます。**これが最後のイベントです。**

```typescript
interface DoneEvent {
  seq: number;
  timestamp: string;
  status: "success" | "error" | "cancelled";  // ステータス
  result: string | null;                       // 最終結果テキスト
  is_error: boolean;                           // エラーかどうか
  errors: string[] | null;                     // エラーメッセージリスト
  usage: UsageInfo;                            // トークン使用量
  cost_usd: string;                            // コスト（USD）
  turn_count: number;                          // ターン数
  duration_ms: number;                         // 実行時間（ミリ秒）
  session_id?: string;                         // セッションID
  messages?: MessageLog[];                     // メッセージログ
  model_usage?: Record<string, UsageInfo>;     // モデル別使用量
}

interface UsageInfo {
  input_tokens: number;
  output_tokens: number;
  cache_creation_5m_tokens: number;
  cache_creation_1h_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
}
```

**例:**
```json
{
  "seq": 50,
  "timestamp": "2024-01-15T10:32:00.123Z",
  "status": "success",
  "result": "CSVファイルの分析が完了しました。データには1000行3列があり...",
  "is_error": false,
  "errors": null,
  "usage": {
    "input_tokens": 5000,
    "output_tokens": 1500,
    "cache_creation_5m_tokens": 0,
    "cache_creation_1h_tokens": 0,
    "cache_read_tokens": 2000,
    "total_tokens": 8500
  },
  "cost_usd": "0.0285",
  "turn_count": 3,
  "duration_ms": 120000,
  "session_id": "sess_abc123def456"
}
```

### 12. error（エラー）

エラー発生時に送信されます。

```typescript
interface ErrorEvent {
  seq: number;
  timestamp: string;
  error_type: string;            // エラータイプ
  message: string;               // エラーメッセージ
  recoverable: boolean;          // 回復可能かどうか
}
```

**例:**
```json
{
  "seq": 8,
  "timestamp": "2024-01-15T10:30:15.789Z",
  "error_type": "tool_execution_error",
  "message": "ファイルが見つかりません: /workspace/missing.csv",
  "recoverable": true
}
```

---

## エラーレスポンス

ストリーミング開始前のエラーは、通常のHTTPレスポンスとして返されます。

**テナント不存在 (404)**
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "テナント 'unknown' が見つかりません"
  }
}
```

**会話不存在 (404)**
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "会話 '...' が見つかりません"
  }
}
```

**会話アーカイブ済み (400)**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "会話 '...' はアーカイブされています"
  }
}
```

**リクエストパースエラー (400)**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "リクエストデータのパースに失敗しました: ..."
  }
}
```

---

## タイムアウトと接続管理

| 設定 | 値 | 説明 |
|------|-----|------|
| イベントタイムアウト | 300秒（5分） | 最後のイベントからこの時間経過でタイムアウト |
| ハートビート間隔 | 10秒 | pingイベント送信間隔 |

### タイムアウト時の動作

```json
{
  "seq": 100,
  "timestamp": "2024-01-15T10:35:00.000Z",
  "error_type": "timeout_error",
  "message": "応答タイムアウト: サーバーからの応答がありません",
  "recoverable": true
}
```

---

## フロントエンド実装例（TypeScript）

### EventSource使用例

```typescript
interface StreamOptions {
  tenantId: string;
  conversationId: string;
  request: StreamRequest;
  apiKey: string;
  onEvent: (event: SSEEvent) => void;
  onError: (error: Error) => void;
  onComplete: () => void;
}

async function streamConversation(options: StreamOptions): Promise<void> {
  const { tenantId, conversationId, request, apiKey, onEvent, onError, onComplete } = options;

  const formData = new FormData();
  formData.append('request_data', JSON.stringify(request));

  const response = await fetch(
    `https://api.example.com/api/tenants/${tenantId}/conversations/${conversationId}/stream`,
    {
      method: 'POST',
      headers: {
        'X-API-Key': apiKey,
      },
      body: formData,
    }
  );

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Stream failed');
  }

  const reader = response.body?.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader!.read();

    if (done) {
      onComplete();
      break;
    }

    buffer += decoder.decode(value, { stream: true });

    // イベントを解析
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    let currentEvent = '';
    let currentData = '';

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7);
      } else if (line.startsWith('data: ')) {
        currentData = line.slice(6);
      } else if (line === '' && currentEvent && currentData) {
        try {
          const event = {
            event: currentEvent,
            data: JSON.parse(currentData),
          };
          onEvent(event);

          // doneイベントで終了
          if (currentEvent === 'done') {
            onComplete();
            return;
          }
        } catch (e) {
          console.error('Failed to parse event:', e);
        }
        currentEvent = '';
        currentData = '';
      }
    }
  }
}
```

### React Hookの例

```typescript
import { useState, useCallback } from 'react';

interface UseStreamingResult {
  isStreaming: boolean;
  events: SSEEvent[];
  result: DoneEvent | null;
  error: Error | null;
  startStream: (userInput: string, files?: File[]) => Promise<void>;
}

function useStreaming(tenantId: string, conversationId: string): UseStreamingResult {
  const [isStreaming, setIsStreaming] = useState(false);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [result, setResult] = useState<DoneEvent | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const startStream = useCallback(async (userInput: string, files?: File[]) => {
    setIsStreaming(true);
    setEvents([]);
    setResult(null);
    setError(null);

    try {
      const formData = new FormData();
      formData.append('request_data', JSON.stringify({
        user_input: userInput,
        executor: {
          user_id: 'current-user',
          name: 'User Name',
          email: 'user@example.com',
        },
      }));

      if (files) {
        files.forEach(file => formData.append('files', file));
      }

      const response = await fetch(
        `/api/tenants/${tenantId}/conversations/${conversationId}/stream`,
        {
          method: 'POST',
          headers: { 'X-API-Key': 'your-api-key' },
          body: formData,
        }
      );

      // ... ストリーム処理

    } catch (e) {
      setError(e as Error);
    } finally {
      setIsStreaming(false);
    }
  }, [tenantId, conversationId]);

  return { isStreaming, events, result, error, startStream };
}
```

---

## イベント処理フローチャート

```
┌─────────────────────────────────────────────────────────────────┐
│                    SSEイベント処理フロー                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  init ─────────► セッション情報を保存                            │
│                  (session_id, tools, model)                     │
│                                                                 │
│  thinking ────► 思考プロセスを表示（オプション）                   │
│                                                                 │
│  assistant ───► テキスト応答を表示                               │
│                 ツール使用ブロックを解析                          │
│                                                                 │
│  tool_call ──► ツール実行中インジケータを表示                     │
│                                                                 │
│  tool_result ► ツール結果を表示                                  │
│                インジケータを更新                                │
│                                                                 │
│  subagent_start/end ► サブエージェント進捗を表示                  │
│                                                                 │
│  progress ───► 進捗バーを更新                                    │
│                                                                 │
│  title ──────► 会話タイトルを更新                                │
│                                                                 │
│  ping ───────► 接続維持（通常は無視してOK）                       │
│                                                                 │
│  done ───────► 最終結果を表示                                    │
│                使用量・コストを記録                               │
│                ストリーム終了                                    │
│                                                                 │
│  error ──────► エラー表示                                        │
│                recoverable=true なら再試行可能                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 関連API

- [会話管理API](./03-conversations.md) - 会話の作成・管理
- [Skills管理API](./05-skills.md) - preferred_skillsで使用するSkill
- [ワークスペースAPI](./07-workspace.md) - アップロードファイルの管理
- [使用状況API](./08-usage.md) - 使用量・コストの確認
