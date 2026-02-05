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
| `file_metadata` | string (JSON) | No | FileUploadMetadataのJSONリスト（デフォルト: `[]`、ファイル添付時は必須） |

### FileUploadMetadata構造

ファイルをアップロードする際は、各ファイルに対応するメタデータを送信する必要があります。

```typescript
interface FileUploadMetadata {
  filename: string;              // 保存用ファイル名（識別子付き）例: route_abcd.ts
  original_name: string;         // 元のファイル名 例: route.ts
  relative_path: string;         // 保存用の相対パス（識別子付き）例: api/users/route_abcd.ts
  original_relative_path: string; // 元の相対パス（表示用）例: api/users/route.ts
  content_type: string;          // MIMEタイプ
  size: number;                  // ファイルサイズ（バイト）
}
```

> **設計方針**: フロントエンドで識別子付きパスを生成し、バックエンドはそのパスをそのまま使用して保存します。
> これにより、同名ファイル（例: `route.ts`）が複数存在する場合でも区別できます。

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
  -F 'files=@data.csv' \
  -F 'file_metadata=[{
    "filename": "data_a1b2.csv",
    "original_name": "data.csv",
    "relative_path": "data_a1b2.csv",
    "original_relative_path": "data.csv",
    "content_type": "text/csv",
    "size": 10240
  }]'
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
| `context_status` | コンテキスト使用状況 | done直前（実行終了時） |
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

> **注**: 現在Extended Thinkingは有効化されていないため、このイベントは送信されません。
> 将来的にExtended Thinkingを有効化した場合に使用されます。

AIの思考プロセスを表示します。Claude SDKの `thinking` オプションが有効な場合のみ送信されます。

```typescript
interface ThinkingEvent {
  seq: number;
  timestamp: string;
  content: string;               // 思考内容
  parent_agent_id?: string;      // 親エージェントID（サブエージェント内の場合のみ）
}
```

**メインエージェントの場合**（`parent_agent_id` は省略）:
```json
{
  "seq": 2,
  "timestamp": "2024-01-15T10:30:01.456Z",
  "content": "ユーザーはCSVファイルの分析を依頼しています。まずファイルの内容を確認し、データの構造を理解する必要があります..."
}
```

**サブエージェント内の場合**:
```json
{
  "seq": 14,
  "timestamp": "2024-01-15T10:30:12.456Z",
  "content": "コードベースを分析中...",
  "parent_agent_id": "tu_subagent_001"
}
```

> **注**: `parent_agent_id` はサブエージェント内の場合のみ含まれます。メインエージェントの場合はフィールド自体が省略されます。

### 3. assistant（アシスタントメッセージ）

AIからのテキスト応答を含みます。`content_blocks` には `text` タイプのブロックのみが含まれます。
ツール使用は別途 `tool_call` イベントとして送信されます。

```typescript
interface AssistantEvent {
  seq: number;
  timestamp: string;
  content_blocks: TextBlock[];       // テキストブロックのリスト
  parent_agent_id?: string;          // 親エージェントID（サブエージェント内の場合のみ）
}

interface TextBlock {
  type: "text";
  text: string;
}
```

**メインエージェントの場合**（`parent_agent_id` は省略）:
```json
{
  "seq": 3,
  "timestamp": "2024-01-15T10:30:02.789Z",
  "content_blocks": [
    {
      "type": "text",
      "text": "CSVファイルを分析します。まずファイルの内容を確認させてください。"
    }
  ]
}
```

**サブエージェント内の場合**:
```json
{
  "seq": 16,
  "timestamp": "2024-01-15T10:30:15.789Z",
  "content_blocks": [
    {
      "type": "text",
      "text": "ファイルを確認しました。"
    }
  ],
  "parent_agent_id": "tu_subagent_001"
}
```

> **注**: `parent_agent_id` はサブエージェント内の場合のみ含まれます。メインエージェントの場合はフィールド自体が省略されます。

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
  parent_agent_id?: string;      // 親エージェントID（サブエージェント内の場合のみ）
}
```

**メインエージェントの場合**（`parent_agent_id` は省略）:
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

**サブエージェント内の場合**:
```json
{
  "seq": 17,
  "timestamp": "2024-01-15T10:30:16.012Z",
  "tool_use_id": "tu_def456",
  "tool_name": "Grep",
  "input": {
    "pattern": "function"
  },
  "summary": "パターン検索",
  "parent_agent_id": "tu_subagent_001"
}
```

> **注**: `parent_agent_id` はサブエージェント内の場合のみ含まれます。

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
  parent_agent_id?: string;      // 親エージェントID（サブエージェント内の場合のみ）
}
```

**メインエージェントの場合**（`parent_agent_id` は省略）:
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

**サブエージェント内の場合**:
```json
{
  "seq": 18,
  "timestamp": "2024-01-15T10:30:16.789Z",
  "tool_use_id": "tu_def456",
  "tool_name": "Grep",
  "status": "completed",
  "content": "3件のマッチが見つかりました",
  "is_error": false,
  "parent_agent_id": "tu_subagent_001"
}
```

> **注**: `parent_agent_id` はサブエージェント内の場合のみ含まれます。

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
  type: "generating" | "tool";               // 進捗タイプ（※thinkingは現在無効）
  message: string;                           // 進捗メッセージ
  tool_use_id?: string;                      // ツール使用ID（tool タイプ時）
  tool_name?: string;                        // ツール名
  tool_status?: "pending" | "running" | "completed" | "error";
  parent_agent_id?: string;                  // 親エージェントID（サブエージェント内の場合のみ）
}
```

**進捗タイプ:**

| type | 説明 | 追加フィールド | 備考 |
|------|------|---------------|------|
| `generating` | テキスト生成中 | なし | |
| `tool` | ツール実行中 | `tool_use_id`, `tool_name`, `tool_status` | |
| `thinking` | AI思考中 | なし | ※Extended Thinking有効時のみ（現在無効） |

> **注**: `type: "thinking"` はExtended Thinkingが有効化された場合のみ送信されます。
> 現在はExtended Thinkingが無効のため、`generating` と `tool` のみが送信されます。

**generating タイプの例:**
```json
{
  "seq": 3,
  "timestamp": "2024-01-15T10:30:02.567Z",
  "type": "generating",
  "message": "回答を作成しています..."
}
```

**tool タイプの例**（メインエージェント）:
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

**tool タイプの例**（サブエージェント内）:
```json
{
  "seq": 19,
  "timestamp": "2024-01-15T10:30:17.567Z",
  "type": "tool",
  "message": "パターン検索中...",
  "tool_use_id": "tu_def456",
  "tool_name": "Grep",
  "tool_status": "running",
  "parent_agent_id": "tu_subagent_001"
}
```

> **注**: `parent_agent_id` はサブエージェント内の場合のみ含まれます。

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

### 11. context_status（コンテキスト使用状況）

コンテキスト使用状況イベント。**`done`イベントの直前**に送信されます。
フロントエンドはこのイベントを受信して、ユーザーに警告を表示したり、入力欄を無効化したりできます。

```typescript
interface ContextStatusEvent {
  seq: number;
  timestamp: string;
  current_context_tokens: number;   // 現在のコンテキストトークン数
  max_context_tokens: number;       // モデルのContext Window上限
  usage_percent: number;            // 使用率（%）
  warning_level: WarningLevel;      // 警告レベル
  can_continue: boolean;            // 次のメッセージを送信可能か
  message?: string;                 // ユーザー向けメッセージ
  recommended_action?: string;      // 推奨アクション
}

type WarningLevel = 'normal' | 'warning' | 'critical' | 'blocked';
```

**警告レベル:**

| warning_level | 使用率 | can_continue | UI表示 |
|---------------|--------|--------------|--------|
| `normal` | < 70% | true | なし |
| `warning` | 70-85% | true | 黄色バナー「新しいチャット推奨」 |
| `critical` | 85-95% | true | オレンジバナー「次の返信でエラーの可能性」 |
| `blocked` | ≥ 95% | **false** | 赤バナー「送信不可」+ 入力欄無効化 |

**例:**
```json
{
  "seq": 49,
  "timestamp": "2024-01-15T10:31:55.123Z",
  "current_context_tokens": 150000,
  "max_context_tokens": 200000,
  "usage_percent": 75.0,
  "warning_level": "warning",
  "can_continue": true,
  "message": "会話が長くなっています。新しいチャットを開始することをおすすめします。",
  "recommended_action": "new_chat"
}
```

**フロントエンド実装ガイド:**

`can_continue: false` の場合：
1. 入力欄を無効化する
2. 「新しいチャットを開始」ボタンを強調表示
3. 会話はリードオンリーとして表示

### 12. done（完了）

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

### 13. error（エラー）

エラー発生時に送信されます。

```typescript
interface ErrorEvent {
  seq: number;
  timestamp: string;
  error_type: ErrorType;         // エラータイプ
  message: string;               // エラーメッセージ
  recoverable: boolean;          // 回復可能かどうか
}

type ErrorType =
  | 'conversation_locked'
  | 'sdk_not_installed'
  | 'model_validation_error'
  | 'options_error'
  | 'execution_error'
  | 'context_limit_exceeded'
  | 'background_execution_error'
  | 'background_task_error'
  | 'timeout_error';
```

**error_type の種類:**

| error_type | 説明 | recoverable |
|------------|------|-------------|
| `conversation_locked` | 会話がロック中（他で実行中） | true |
| `sdk_not_installed` | SDKがインストールされていない | false |
| `model_validation_error` | モデルバリデーションエラー | false |
| `options_error` | SDK オプション構築エラー | false |
| `execution_error` | 実行中のエラー | false |
| `context_limit_exceeded` | コンテキスト制限超過（新しいチャットが必要） | false |
| `background_execution_error` | バックグラウンド実行エラー | false |
| `background_task_error` | バックグラウンドタスクエラー | false |
| `timeout_error` | タイムアウト | true |

**`context_limit_exceeded` エラー発生時のUI対応:**
- このエラーは会話のコンテキストがモデルの上限に達した場合に発生
- 「新しいチャットを開始してください」というメッセージを表示
- 入力欄を無効化し、新規会話作成ボタンを強調表示

**例:**
```json
{
  "seq": 8,
  "timestamp": "2024-01-15T10:30:15.789Z",
  "error_type": "execution_error",
  "message": "ファイルが見つかりません: /workspace/missing.csv",
  "recoverable": false
}
```

**コンテキスト制限超過の例:**
```json
{
  "seq": 1,
  "timestamp": "2024-01-15T10:35:00.789Z",
  "error_type": "context_limit_exceeded",
  "message": "コンテキストトークン数が上限を超えました。新しいチャットを開始してください。",
  "recoverable": false
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
│  context_status ► コンテキスト使用状況を確認                      │
│                   can_continue=falseなら入力を無効化              │
│                                                                 │
│  done ───────► 最終結果を表示                                    │
│                使用量・コストを記録                               │
│                ストリーム終了                                    │
│                                                                 │
│  error ──────► エラー表示                                        │
│                recoverable=true なら再試行可能                   │
│                context_limit_exceeded なら新しいチャットを促す    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 関連API

- [会話管理API](./03-conversations.md) - 会話の作成・管理
- [Skills管理API](./05-skills.md) - preferred_skillsで使用するSkill
- [ワークスペースAPI](./07-workspace.md) - アップロードファイルの管理
- [使用状況API](./08-usage.md) - 使用量・コストの確認
