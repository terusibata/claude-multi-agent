# ストリーミング仕様書

Claude Multi-Agent のSSE（Server-Sent Events）ストリーミング仕様書です。

## 目次

- [概要](#概要)
- [イベントタイプ](#イベントタイプ)
- [メッセージ形式](#メッセージ形式)
- [フロー図](#フロー図)
- [Next.js型定義](#nextjs型定義)
- [クライアント実装例](#クライアント実装例)

## 概要

### エンドポイント

```
POST /api/tenants/{tenant_id}/conversations/{conversation_id}/stream
```

### レスポンス形式

Server-Sent Events (SSE) 形式でストリーミングします。

```
event: message
data: {"type": "system", "subtype": "init", ...}

event: message
data: {"type": "assistant", "content_blocks": [...]}

event: message
data: {"type": "result", "subtype": "success", ...}
```

### 接続特性

- **タイムアウト**: 300秒
- **バックグラウンド実行**: クライアント切断後も処理は継続
- **メッセージ順序**: 保証される
- **ハートビート**: 10秒間隔で送信

## イベントタイプ

### 1. message イベント

メインのメッセージイベント。`type` フィールドで種類を区別します。

| type | 説明 |
|------|------|
| `system` | システムメッセージ（初期化など） |
| `assistant` | アシスタントからのメッセージ（テキスト、ツール使用、思考） |
| `user_result` | ツール実行結果 |
| `result` | 最終結果 |

### 2. error イベント

エラー発生時に送信されます。

### 3. title_generated イベント

初回実行時、タイトルが自動生成された際に送信されます。

### 4. status イベント（リアルタイム進捗）

現在の処理状態を通知します。UIでの進捗表示に使用します。

| state | 説明 |
|-------|------|
| `thinking` | 思考中 |
| `generating` | レスポンス生成中 |
| `tool_execution` | ツール実行中 |
| `waiting` | 待機中 |

### 5. heartbeat イベント

接続維持のために定期的に送信されます。10秒間隔で送信されます。

### 6. turn_progress イベント

ターン進捗を通知します。AssistantMessageごとに送信されます。

### 7. tool_progress イベント

ツール実行の進捗を通知します。`parent_tool_use_id`でサブエージェント内のツールを識別できます。

| status | 説明 |
|--------|------|
| `pending` | 受付済み |
| `running` | 実行中 |
| `completed` | 完了 |
| `error` | エラー |

**注意**: `status`イベントはメインエージェントのみで送信されます。サブエージェント内では`tool_progress`イベントの`parent_tool_use_id`で親子関係を追跡できます。

### 8. subagent イベント

Taskツールによるサブエージェントの開始/終了を通知します。

## メッセージ形式

### System Message (type: "system")

#### subtype: "init"

セッション初期化メッセージ。

```json
{
  "type": "system",
  "subtype": "init",
  "timestamp": "2024-01-01T00:00:00.000000",
  "data": {
    "session_id": "session-uuid-from-sdk",
    "conversation_id": "conversation-uuid",
    "tools": ["Read", "Write", "Bash", "Glob", "Grep"],
    "model": "Claude Sonnet 4",
    "tenant_config": {
      "tenant_id": "tenant-001",
      "system_prompt_length": 50,
      "system_prompt_preview": "あなたは親切なアシスタントです。..."
    },
    "model_config": {
      "model_id": "claude-sonnet-4",
      "display_name": "Claude Sonnet 4",
      "bedrock_model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "model_region": "us-west-2"
    }
  }
}
```

### Assistant Message (type: "assistant")

#### テキストブロック

```json
{
  "type": "assistant",
  "subtype": null,
  "timestamp": "2024-01-01T00:00:00.000000",
  "content_blocks": [
    {
      "type": "text",
      "text": "こんにちは！お手伝いします。"
    }
  ]
}
```

#### ツール使用ブロック

```json
{
  "type": "assistant",
  "subtype": null,
  "timestamp": "2024-01-01T00:00:00.000000",
  "content_blocks": [
    {
      "type": "tool_use",
      "id": "tool-use-uuid",
      "name": "Read",
      "input": {
        "file_path": "/path/to/file.py"
      },
      "summary": "ファイルを読み取り: file.py"
    }
  ]
}
```

#### 思考ブロック（Extended Thinking）

```json
{
  "type": "assistant",
  "subtype": null,
  "timestamp": "2024-01-01T00:00:00.000000",
  "content_blocks": [
    {
      "type": "thinking",
      "text": "ユーザーの要求を分析しています..."
    }
  ]
}
```

### User Result Message (type: "user_result")

ツール実行結果。

```json
{
  "type": "user_result",
  "subtype": null,
  "timestamp": "2024-01-01T00:00:00.000000",
  "content_blocks": [
    {
      "type": "tool_result",
      "tool_use_id": "tool-use-uuid",
      "tool_name": "Read",
      "content": "ファイルの内容...",
      "is_error": false,
      "status": "completed"
    }
  ]
}
```

### Result Message (type: "result")

最終結果メッセージ。`messages`には`/api/tenants/{tenant_id}/conversations/{conversation_id}/messages`と同じ形式のメッセージログが含まれます。

```json
{
  "type": "result",
  "subtype": "success",
  "timestamp": "2024-01-01T00:00:00.000000",
  "result": "完了しました。",
  "is_error": false,
  "errors": null,
  "usage": {
    "input_tokens": 1500,
    "output_tokens": 500,
    "cache_creation_tokens": 15000,
    "cache_read_tokens": 200,
    "total_tokens": 2000,
    "cache_creation": {
      "ephemeral_1h_input_tokens": 0,
      "ephemeral_5m_input_tokens": 15000
    }
  },
  "total_cost_usd": 0.0075,
  "num_turns": 3,
  "duration_ms": 5230,
  "session_id": "session-uuid-from-sdk",
  "messages": [
    {
      "type": "system",
      "subtype": "init",
      "timestamp": "2024-01-01T00:00:00.000000",
      "data": {...}
    },
    {
      "type": "assistant",
      "subtype": null,
      "timestamp": "2024-01-01T00:00:01.000000",
      "content_blocks": [...]
    }
  ],
  "model_usage": null
}
```

#### usage フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `input_tokens` | number | 入力トークン数 |
| `output_tokens` | number | 出力トークン数 |
| `cache_creation_tokens` | number | キャッシュ作成トークン合計 |
| `cache_read_tokens` | number | キャッシュ読み込みトークン |
| `total_tokens` | number | 合計トークン数（入力+出力） |
| `cache_creation` | object | ephemeralキャッシュの内訳（オプション） |

#### cache_creation 内訳（ephemeral cache）

| フィールド | 説明 |
|-----------|------|
| `ephemeral_1h_input_tokens` | 1時間キャッシュのトークン数 |
| `ephemeral_5m_input_tokens` | 5分キャッシュのトークン数 |

#### model_usage について

**注意**: `model_usage`フィールドはTypeScript SDKでのみ利用可能です。Python SDKでは常に`null`が返されます。サブエージェント（Task）が使用するモデル別のトークン使用量を追跡するには、TypeScript SDKの使用を検討してください。

#### subtype の種類

| subtype | 説明 |
|---------|------|
| `success` | 正常完了 |
| `error_during_execution` | 実行中にエラー発生 |

### Error Event

```json
{
  "type": "error",
  "message": "エラーメッセージ",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### Title Generated Event

```json
{
  "title": "生成されたタイトル",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### Status Event（リアルタイム進捗）

現在の処理状態を通知します。

```json
{
  "state": "thinking",
  "message": "思考中...",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### Heartbeat Event

接続維持のために定期的に送信されます。

```json
{
  "timestamp": "2024-01-01T00:00:00.000000",
  "elapsed_ms": 15000
}
```

### Turn Progress Event

ターン進捗を通知します。

```json
{
  "current_turn": 2,
  "max_turns": 10,
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### Tool Progress Event

ツール実行の進捗を通知します。

```json
{
  "tool_use_id": "tool-use-uuid",
  "tool_name": "Read",
  "status": "running",
  "message": "ファイルを読み取り中...",
  "parent_tool_use_id": null,
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

サブエージェント内のツールの場合:

```json
{
  "tool_use_id": "child-tool-uuid",
  "tool_name": "Grep",
  "status": "running",
  "message": "検索中...",
  "parent_tool_use_id": "task-tool-uuid",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### Subagent Event

Taskツールによるサブエージェントの開始/終了を通知します。

```json
{
  "action": "start",
  "agent_type": "Explore",
  "description": "コードベースを探索中",
  "parent_tool_use_id": "task-tool-uuid",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

終了時:

```json
{
  "action": "stop",
  "agent_type": "Explore",
  "description": "コードベースを探索中",
  "parent_tool_use_id": "task-tool-uuid",
  "result": "ファイルが見つかりました",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

## フロー図

```
Client                          Server
  |                               |
  |  POST /conversations/{id}/stream
  |------------------------------>|
  |                               |
  |  event: message               |
  |  data: {type: "system", subtype: "init", ...}
  |<------------------------------|
  |                               |
  |  event: turn_progress         |
  |  data: {current_turn: 1, ...} |
  |<------------------------------|
  |                               |
  |  event: status                |
  |  data: {state: "thinking", ...}
  |<------------------------------|
  |                               |
  |  event: message (thinking)    |
  |  data: {type: "assistant", content_blocks: [{type: "thinking", ...}]}
  |<------------------------------|
  |                               |
  |  event: status                |
  |  data: {state: "generating", ...}
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "assistant", content_blocks: [{type: "text", ...}]}
  |<------------------------------|
  |                               |
  |  event: heartbeat             |
  |  data: {elapsed_ms: 10000, ...}
  |<------------------------------|
  |                               |
  |  event: status                |
  |  data: {state: "tool_execution", ...}
  |<------------------------------|
  |                               |
  |  event: tool_progress         |
  |  data: {status: "pending", tool_name: "Read", ...}
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "assistant", content_blocks: [{type: "tool_use", ...}]}
  |<------------------------------|
  |                               |
  |  event: tool_progress         |
  |  data: {status: "running", ...}
  |<------------------------------|
  |                               |
  |  event: tool_progress         |
  |  data: {status: "completed", ...}
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "user_result", content_blocks: [{type: "tool_result", ...}]}
  |<------------------------------|
  |                               |
  |  event: heartbeat             |
  |  data: {elapsed_ms: 20000, ...}
  |<------------------------------|
  |                               |
  |  event: turn_progress         |
  |  data: {current_turn: 2, ...} |
  |<------------------------------|
  |                               |
  |  event: status                |
  |  data: {state: "generating", ...}
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "assistant", content_blocks: [{type: "text", ...}]}
  |<------------------------------|
  |                               |
  |  event: title_generated       |
  |  data: {title: "...", ...}    |
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "result", subtype: "success", ...}
  |<------------------------------|
  |                               |
  |  (connection closed)          |
  |<------------------------------|
```

## Next.js型定義

以下の型定義をNext.jsプロジェクトで使用してください。

```typescript
// types/streaming.ts

// ==========================================
// 基本型
// ==========================================

export type MessageType = 'system' | 'assistant' | 'user_result' | 'result' | 'unknown';

export type SystemSubtype = 'init' | 'finish';

export type ResultSubtype = 'success' | 'error_during_execution';

// ==========================================
// コンテンツブロック
// ==========================================

export interface TextBlock {
  type: 'text';
  text: string;
}

export interface ToolUseBlock {
  type: 'tool_use';
  id: string;
  name: string;
  input: Record<string, unknown>;
  summary?: string;
}

export interface ThinkingBlock {
  type: 'thinking';
  text: string;
}

export interface ToolResultBlock {
  type: 'tool_result';
  tool_use_id: string;
  tool_name: string;
  content: string;
  is_error: boolean;
  status: 'completed' | 'error';
}

export type ContentBlock = TextBlock | ToolUseBlock | ThinkingBlock | ToolResultBlock;

// ==========================================
// 使用状況
// ==========================================

export interface CacheCreationInfo {
  ephemeral_1h_input_tokens: number;
  ephemeral_5m_input_tokens: number;
}

export interface UsageInfo {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  cache_creation?: CacheCreationInfo;  // ephemeralキャッシュの内訳
}

// ==========================================
// 設定情報
// ==========================================

export interface TenantConfigInfo {
  tenant_id: string;
  system_prompt_length: number;
  system_prompt_preview: string;
}

export interface ModelConfigInfo {
  model_id: string;
  display_name: string;
  bedrock_model_id: string;
  model_region: string;
}

// ==========================================
// メッセージ型
// ==========================================

export interface SystemInitData {
  session_id: string;
  conversation_id?: string;
  tools: string[];
  model: string;
  tenant_config?: TenantConfigInfo;
  model_config?: ModelConfigInfo;
}

export interface SystemMessage {
  type: 'system';
  subtype: SystemSubtype;
  timestamp: string;
  data: SystemInitData;
}

export interface AssistantMessage {
  type: 'assistant';
  subtype: null;
  timestamp: string;
  content_blocks: ContentBlock[];
}

export interface UserResultMessage {
  type: 'user_result';
  subtype: null;
  timestamp: string;
  content_blocks: ToolResultBlock[];
}

export interface ModelUsageInfo {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

export interface ResultMessage {
  type: 'result';
  subtype: ResultSubtype;
  timestamp: string;
  result: string | null;
  is_error: boolean;
  errors: string[] | null;
  usage: UsageInfo;
  total_cost_usd: number;
  num_turns: number;
  duration_ms: number;
  session_id?: string;
  messages?: StreamingMessage[];
  model_usage?: Record<string, ModelUsageInfo>;
}

export type StreamingMessage =
  | SystemMessage
  | AssistantMessage
  | UserResultMessage
  | ResultMessage;

// ==========================================
// イベント型
// ==========================================

export interface MessageEvent {
  event: 'message';
  data: StreamingMessage;
}

export interface ErrorEvent {
  event: 'error';
  data: {
    type: string;
    message: string;
    timestamp: string;
  };
}

export interface TitleGeneratedEvent {
  event: 'title_generated';
  data: {
    title: string;
    timestamp: string;
  };
}

// ==========================================
// リアルタイム進捗イベント型
// ==========================================

export type StatusState = 'thinking' | 'generating' | 'tool_execution' | 'waiting';

export interface StatusEvent {
  event: 'status';
  data: {
    state: StatusState;
    message: string;
    timestamp: string;
  };
}

export interface HeartbeatEvent {
  event: 'heartbeat';
  data: {
    timestamp: string;
    elapsed_ms: number;
  };
}

export interface TurnProgressEvent {
  event: 'turn_progress';
  data: {
    current_turn: number;
    max_turns: number | null;
    timestamp: string;
  };
}

export type ToolProgressStatus = 'pending' | 'running' | 'completed' | 'error';

export interface ToolProgressEvent {
  event: 'tool_progress';
  data: {
    tool_use_id: string;
    tool_name: string;
    status: ToolProgressStatus;
    message?: string;
    parent_tool_use_id: string | null;
    timestamp: string;
  };
}

export type SubagentAction = 'start' | 'stop';

export interface SubagentEvent {
  event: 'subagent';
  data: {
    action: SubagentAction;
    agent_type: string;
    description: string;
    parent_tool_use_id: string;
    result?: string;
    timestamp: string;
  };
}

export type StreamingEvent =
  | MessageEvent
  | ErrorEvent
  | TitleGeneratedEvent
  | StatusEvent
  | HeartbeatEvent
  | TurnProgressEvent
  | ToolProgressEvent
  | SubagentEvent;

// ==========================================
// リクエスト型
// ==========================================

export interface ExecutorInfo {
  user_id: string;
  name: string;
  email: string;
  employee_id?: string;
}

export interface StreamRequest {
  user_input: string;
  executor: ExecutorInfo;
  tokens?: Record<string, string>;
  preferred_skills?: string[];
}

// ==========================================
// 型ガード
// ==========================================

export function isSystemMessage(msg: StreamingMessage): msg is SystemMessage {
  return msg.type === 'system';
}

export function isAssistantMessage(msg: StreamingMessage): msg is AssistantMessage {
  return msg.type === 'assistant';
}

export function isUserResultMessage(msg: StreamingMessage): msg is UserResultMessage {
  return msg.type === 'user_result';
}

export function isResultMessage(msg: StreamingMessage): msg is ResultMessage {
  return msg.type === 'result';
}

export function isTextBlock(block: ContentBlock): block is TextBlock {
  return block.type === 'text';
}

export function isToolUseBlock(block: ContentBlock): block is ToolUseBlock {
  return block.type === 'tool_use';
}

export function isThinkingBlock(block: ContentBlock): block is ThinkingBlock {
  return block.type === 'thinking';
}

export function isToolResultBlock(block: ContentBlock): block is ToolResultBlock {
  return block.type === 'tool_result';
}
```

## クライアント実装例

### Next.js (TypeScript)

```typescript
// hooks/useStreaming.ts

import { useState, useCallback, useRef } from 'react';
import type {
  StreamRequest,
  StreamingMessage,
  ContentBlock,
  UsageInfo,
} from '@/types/streaming';

interface StreamingHandlers {
  onSystem?: (message: StreamingMessage) => void;
  onAssistant?: (message: StreamingMessage) => void;
  onUserResult?: (message: StreamingMessage) => void;
  onResult?: (message: StreamingMessage) => void;
  onError?: (error: { message: string }) => void;
  onTitleGenerated?: (title: string) => void;
}

interface StreamingState {
  isStreaming: boolean;
  sessionId: string | null;
  messages: StreamingMessage[];
  currentText: string;
  tools: string[];
  usage: UsageInfo | null;
  error: string | null;
}

export function useStreaming(tenantId: string, conversationId: string) {
  const [state, setState] = useState<StreamingState>({
    isStreaming: false,
    sessionId: null,
    messages: [],
    currentText: '',
    tools: [],
    usage: null,
    error: null,
  });

  const abortControllerRef = useRef<AbortController | null>(null);

  const execute = useCallback(
    async (request: StreamRequest, handlers?: StreamingHandlers) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      abortControllerRef.current = new AbortController();

      setState(prev => ({
        ...prev,
        isStreaming: true,
        error: null,
        currentText: '',
      }));

      try {
        const formData = new FormData();
        formData.append('request_data', JSON.stringify(request));

        const response = await fetch(
          `/api/tenants/${tenantId}/conversations/${conversationId}/stream`,
          {
            method: 'POST',
            body: formData,
            signal: abortControllerRef.current.signal,
          }
        );

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('Response body is null');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          let eventType = '';
          let eventData = '';

          for (const line of lines) {
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              eventData = line.slice(5).trim();
            } else if (line === '' && eventType && eventData) {
              processEvent(eventType, eventData, handlers, setState);
              eventType = '';
              eventData = '';
            }
          }
        }
      } catch (error) {
        if ((error as Error).name === 'AbortError') {
          console.log('Request was aborted');
        } else {
          setState(prev => ({
            ...prev,
            error: (error as Error).message,
          }));
        }
      } finally {
        setState(prev => ({ ...prev, isStreaming: false }));
      }
    },
    [tenantId, conversationId]
  );

  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }, []);

  return {
    ...state,
    execute,
    cancel,
  };
}

function processEvent(
  eventType: string,
  eventData: string,
  handlers: StreamingHandlers | undefined,
  setState: React.Dispatch<React.SetStateAction<StreamingState>>
) {
  try {
    const data = JSON.parse(eventData);

    if (eventType === 'message') {
      const message = data as StreamingMessage;

      setState(prev => ({
        ...prev,
        messages: [...prev.messages, message],
      }));

      switch (message.type) {
        case 'system':
          if (message.subtype === 'init') {
            setState(prev => ({
              ...prev,
              sessionId: message.data.session_id,
              tools: message.data.tools,
            }));
          }
          handlers?.onSystem?.(message);
          break;

        case 'assistant':
          for (const block of message.content_blocks) {
            if (block.type === 'text') {
              setState(prev => ({
                ...prev,
                currentText: prev.currentText + block.text,
              }));
            }
          }
          handlers?.onAssistant?.(message);
          break;

        case 'user_result':
          handlers?.onUserResult?.(message);
          break;

        case 'result':
          setState(prev => ({
            ...prev,
            usage: message.usage,
          }));
          handlers?.onResult?.(message);
          break;
      }
    } else if (eventType === 'error') {
      setState(prev => ({
        ...prev,
        error: data.message,
      }));
      handlers?.onError?.(data);
    } else if (eventType === 'title_generated') {
      handlers?.onTitleGenerated?.(data.title);
    }
  } catch (e) {
    console.error('Failed to parse event data:', e);
  }
}
```

### 使用例

```tsx
// components/Chat.tsx

import { useStreaming } from '@/hooks/useStreaming';
import { useState } from 'react';

export function Chat({ tenantId, conversationId }: { tenantId: string; conversationId: string }) {
  const [input, setInput] = useState('');
  const {
    isStreaming,
    currentText,
    usage,
    error,
    execute,
    cancel,
  } = useStreaming(tenantId, conversationId);

  const handleSubmit = async () => {
    if (!input.trim() || isStreaming) return;

    await execute({
      user_input: input,
      executor: {
        user_id: 'user-001',
        name: 'User',
        email: 'user@example.com',
      },
    }, {
      onTitleGenerated: (title) => {
        console.log('Title generated:', title);
      },
    });

    setInput('');
  };

  return (
    <div>
      <div className="messages">
        {currentText && <div className="assistant">{currentText}</div>}
      </div>

      {error && <div className="error">{error}</div>}

      {usage && (
        <div className="usage">
          Tokens: {usage.total_tokens}
        </div>
      )}

      <div className="input">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={isStreaming}
        />
        <button onClick={handleSubmit} disabled={isStreaming}>
          {isStreaming ? 'Sending...' : 'Send'}
        </button>
        {isStreaming && (
          <button onClick={cancel}>Cancel</button>
        )}
      </div>
    </div>
  );
}
```
