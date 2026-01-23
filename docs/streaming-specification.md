# ストリーミング仕様書 v2

Claude Multi-Agent のSSE（Server-Sent Events）ストリーミング仕様書です。

## 目次

- [概要](#概要)
- [イベントタイプ](#イベントタイプ)
- [イベント形式](#イベント形式)
- [フロー図](#フロー図)
- [Next.js型定義](#nextjs型定義)
- [クライアント実装例](#クライアント実装例)

## 概要

### エンドポイント

```
POST /api/tenants/{tenant_id}/conversations/{conversation_id}/stream
```

### レスポンス形式

Server-Sent Events (SSE) 形式でストリーミングします。全てのイベントに**シーケンス番号（seq）**が付与され、順序保証を提供します。

**重要**: JSONデータ内にも `event` フィールドを含めます。これにより、SSEの `event:` ヘッダーをパースできない環境でもイベントタイプを判別できます。

```
event: init
data: {"seq": 1, "event": "init", "timestamp": "...", "session_id": "...", ...}

event: thinking
data: {"seq": 2, "event": "thinking", "timestamp": "...", "content": "..."}

event: assistant
data: {"seq": 3, "event": "assistant", "timestamp": "...", "content_blocks": [...]}

event: done
data: {"seq": 99, "event": "done", "timestamp": "...", "status": "success", ...}
```

### 接続特性

- **タイムアウト**: 300秒
- **バックグラウンド実行**: クライアント切断後も処理は継続
- **メッセージ順序**: `seq`番号で保証される
- **pingイベント**: 10秒間隔で送信

## イベントタイプ

| イベント | 説明 | 送信タイミング |
|---------|------|--------------|
| `init` | セッション初期化 | 開始時1回 |
| `thinking` | Extended Thinking | 思考ブロック受信時 |
| `assistant` | テキストコンテンツ | アシスタントメッセージ時 |
| `tool_call` | ツール呼び出し開始 | ツール使用決定時 |
| `tool_result` | ツール実行結果 | 結果取得時 |
| `subagent_start` | サブエージェント開始 | Task開始時 |
| `subagent_end` | サブエージェント終了 | Task完了時 |
| `progress` | 進捗更新（統合型） | 状態変化時 |
| `title` | タイトル生成 | 初回実行時 |
| `ping` | ハートビート | 10秒間隔 |
| `done` | 完了 | 終了時 |
| `error` | エラー | エラー発生時 |

## イベント形式

### 共通構造

全てのイベントは以下の共通フィールドを持ちます：

```json
{
  "seq": 1,
  "timestamp": "2024-01-01T00:00:00.000000Z",
  "event": "イベントタイプ名"
}
```

**注記**: `event` フィールドは SSE の `event:` ヘッダーと同じ値を持ちます。これにより、クライアント側で以下の2つの方法でイベントタイプを判別できます：

1. **EventSource API 使用時**: `event.type` プロパティ
2. **生のストリームパース時**: JSON の `event` フィールド

### init イベント

セッション初期化イベント。

```json
{
  "event": "init",
  "data": {
    "seq": 1,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "session_id": "session-uuid-from-sdk",
    "tools": ["Read", "Write", "Bash", "Glob", "Grep"],
    "model": "Claude Sonnet 4",
    "conversation_id": "conversation-uuid"
  }
}
```

### thinking イベント

Extended Thinking（思考プロセス）イベント。

```json
{
  "event": "thinking",
  "data": {
    "seq": 2,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "content": "ユーザーの要求を分析しています...",
    "parent_agent_id": null
  }
}
```

サブエージェント内の場合：

```json
{
  "event": "thinking",
  "data": {
    "seq": 15,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "content": "コードベースを分析中...",
    "parent_agent_id": "task-tool-uuid"
  }
}
```

### assistant イベント

テキストコンテンツイベント。

```json
{
  "event": "assistant",
  "data": {
    "seq": 3,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "content_blocks": [
      {
        "type": "text",
        "text": "こんにちは！お手伝いします。"
      }
    ],
    "parent_agent_id": null
  }
}
```

### tool_call イベント

ツール呼び出しイベント。

```json
{
  "event": "tool_call",
  "data": {
    "seq": 4,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "tool_use_id": "tool-use-uuid",
    "tool_name": "Read",
    "input": {
      "file_path": "/path/to/file.py"
    },
    "summary": "ファイルを読み取り: file.py",
    "parent_agent_id": null
  }
}
```

### tool_result イベント

ツール実行結果イベント。

```json
{
  "event": "tool_result",
  "data": {
    "seq": 6,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "tool_use_id": "tool-use-uuid",
    "tool_name": "Read",
    "status": "completed",
    "content": "ファイルの内容プレビュー...",
    "is_error": false,
    "parent_agent_id": null
  }
}
```

### subagent_start イベント

サブエージェント開始イベント。

```json
{
  "event": "subagent_start",
  "data": {
    "seq": 7,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "agent_id": "task-tool-uuid",
    "agent_type": "Explore",
    "description": "コードベースを探索中",
    "model": "claude-3-5-haiku-20241022"
  }
}
```

### subagent_end イベント

サブエージェント終了イベント。

```json
{
  "event": "subagent_end",
  "data": {
    "seq": 20,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "agent_id": "task-tool-uuid",
    "agent_type": "Explore",
    "status": "completed",
    "result_preview": "ファイルが見つかりました"
  }
}
```

### progress イベント

統合型の進捗イベント。複数のタイプ（thinking, generating, tool）を1つのイベント形式で通知します。

#### thinking（思考中）

```json
{
  "event": "progress",
  "data": {
    "seq": 2,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "type": "thinking",
    "message": "思考中..."
  }
}
```

#### generating（テキスト生成中）

```json
{
  "event": "progress",
  "data": {
    "seq": 3,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "type": "generating",
    "message": "レスポンスを生成中..."
  }
}
```

#### tool（ツール実行）

```json
{
  "event": "progress",
  "data": {
    "seq": 5,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "type": "tool",
    "message": "Readを実行中...",
    "tool_use_id": "tool-use-uuid",
    "tool_name": "Read",
    "tool_status": "running",
    "parent_agent_id": null
  }
}
```

ツールステータス:
- `pending`: 受付済み
- `running`: 実行中
- `completed`: 完了
- `error`: エラー

### title イベント

タイトル生成イベント。初回実行時のみ送信されます。

```json
{
  "event": "title",
  "data": {
    "seq": 50,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "title": "生成されたタイトル"
  }
}
```

### ping イベント

ハートビートイベント。接続維持のために10秒間隔で送信されます。

```json
{
  "event": "ping",
  "data": {
    "seq": 0,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "elapsed_ms": 15000
  }
}
```

### done イベント

完了イベント。処理終了時に送信されます。

```json
{
  "event": "done",
  "data": {
    "seq": 99,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "status": "success",
    "result": "完了しました。",
    "is_error": false,
    "errors": null,
    "usage": {
      "input_tokens": 1500,
      "output_tokens": 500,
      "cache_creation_5m_tokens": 15000,
      "cache_creation_1h_tokens": 0,
      "cache_read_tokens": 200,
      "total_tokens": 2000
    },
    "cost_usd": "0.0075",
    "turn_count": 3,
    "duration_ms": 5230,
    "session_id": "session-uuid-from-sdk",
    "messages": [...],
    "model_usage": {
      "claude-sonnet-4-20250514": {
        "input_tokens": 1000,
        "output_tokens": 400,
        "cache_creation_5m_input_tokens": 15000,
        "cache_creation_1h_input_tokens": 0,
        "cache_read_input_tokens": 200,
        "cost_usd": "0.005"
      },
      "claude-3-5-haiku-20241022": {
        "input_tokens": 500,
        "output_tokens": 100,
        "cache_creation_5m_input_tokens": 0,
        "cache_creation_1h_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cost_usd": "0.0025"
      }
    }
  }
}
```

#### status の種類

| status | 説明 |
|--------|------|
| `success` | 正常完了 |
| `error` | エラー発生 |
| `cancelled` | キャンセル |

### error イベント

エラーイベント。

```json
{
  "event": "error",
  "data": {
    "seq": 1,
    "timestamp": "2024-01-01T00:00:00.000000Z",
    "error_type": "execution_error",
    "message": "エラーメッセージ",
    "recoverable": false
  }
}
```

#### error_type の種類

| error_type | 説明 | recoverable |
|------------|------|-------------|
| `conversation_locked` | 会話がロック中 | true |
| `sdk_not_installed` | SDKがインストールされていない | false |
| `model_validation_error` | モデルバリデーションエラー | false |
| `options_error` | SDK オプション構築エラー | false |
| `execution_error` | 実行中のエラー | false |
| `background_execution_error` | バックグラウンド実行エラー | false |
| `background_task_error` | バックグラウンドタスクエラー | false |
| `timeout_error` | タイムアウト | true |

## フロー図

```
Client                          Server
  |                               |
  |  POST /conversations/{id}/stream
  |------------------------------>|
  |                               |
  |  event: init                  |  (seq: 1)
  |<------------------------------|
  |                               |
  |  event: progress              |  (seq: 2, type: "thinking")
  |<------------------------------|
  |                               |
  |  event: thinking              |  (seq: 3)
  |<------------------------------|
  |                               |
  |  event: progress              |  (seq: 4, type: "generating")
  |<------------------------------|
  |                               |
  |  event: assistant             |  (seq: 5, text)
  |<------------------------------|
  |                               |
  |  event: progress              |  (seq: 6, type: "tool", status: "pending")
  |<------------------------------|
  |                               |
  |  event: tool_call             |  (seq: 7)
  |<------------------------------|
  |                               |
  |  event: progress              |  (seq: 8, type: "tool", status: "running")
  |<------------------------------|
  |                               |
  |  event: ping                  |  (seq: 0, heartbeat)
  |<------------------------------|
  |                               |
  |  event: progress              |  (seq: 9, type: "tool", status: "completed")
  |<------------------------------|
  |                               |
  |  event: tool_result           |  (seq: 10)
  |<------------------------------|
  |                               |
  |  event: assistant             |  (seq: 11, text)
  |<------------------------------|
  |                               |
  |  event: title                 |  (seq: 12)
  |<------------------------------|
  |                               |
  |  event: done                  |  (seq: 13)
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

/** イベント共通フィールド */
export interface BaseEventData {
  seq: number;
  timestamp: string;
}

// ==========================================
// init イベント
// ==========================================

export interface InitEventData extends BaseEventData {
  session_id: string;
  tools: string[];
  model: string;
  conversation_id?: string;
}

export interface InitEvent {
  event: 'init';
  data: InitEventData;
}

// ==========================================
// thinking イベント
// ==========================================

export interface ThinkingEventData extends BaseEventData {
  content: string;
  parent_agent_id?: string | null;
}

export interface ThinkingEvent {
  event: 'thinking';
  data: ThinkingEventData;
}

// ==========================================
// assistant イベント
// ==========================================

export interface TextBlock {
  type: 'text';
  text: string;
}

export type ContentBlock = TextBlock;

export interface AssistantEventData extends BaseEventData {
  content_blocks: ContentBlock[];
  parent_agent_id?: string | null;
}

export interface AssistantEvent {
  event: 'assistant';
  data: AssistantEventData;
}

// ==========================================
// tool_call イベント
// ==========================================

export interface ToolCallEventData extends BaseEventData {
  tool_use_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  summary: string;
  parent_agent_id?: string | null;
}

export interface ToolCallEvent {
  event: 'tool_call';
  data: ToolCallEventData;
}

// ==========================================
// tool_result イベント
// ==========================================

export type ToolStatus = 'completed' | 'error';

export interface ToolResultEventData extends BaseEventData {
  tool_use_id: string;
  tool_name: string;
  status: ToolStatus;
  content: string;
  is_error: boolean;
  parent_agent_id?: string | null;
}

export interface ToolResultEvent {
  event: 'tool_result';
  data: ToolResultEventData;
}

// ==========================================
// subagent イベント
// ==========================================

export interface SubagentStartEventData extends BaseEventData {
  agent_id: string;
  agent_type: string;
  description: string;
  model?: string;
}

export interface SubagentStartEvent {
  event: 'subagent_start';
  data: SubagentStartEventData;
}

export interface SubagentEndEventData extends BaseEventData {
  agent_id: string;
  agent_type: string;
  status: ToolStatus;
  result_preview?: string;
}

export interface SubagentEndEvent {
  event: 'subagent_end';
  data: SubagentEndEventData;
}

// ==========================================
// progress イベント
// ==========================================

export type ProgressType = 'thinking' | 'generating' | 'tool';
export type ToolProgressStatus = 'pending' | 'running' | 'completed' | 'error';

export interface ProgressEventData extends BaseEventData {
  type: ProgressType;
  message: string;
  tool_use_id?: string;
  tool_name?: string;
  tool_status?: ToolProgressStatus;
  parent_agent_id?: string | null;
}

export interface ProgressEvent {
  event: 'progress';
  data: ProgressEventData;
}

// ==========================================
// title イベント
// ==========================================

export interface TitleEventData extends BaseEventData {
  title: string;
}

export interface TitleEvent {
  event: 'title';
  data: TitleEventData;
}

// ==========================================
// ping イベント
// ==========================================

export interface PingEventData extends BaseEventData {
  elapsed_ms: number;
}

export interface PingEvent {
  event: 'ping';
  data: PingEventData;
}

// ==========================================
// done イベント
// ==========================================

export interface UsageInfo {
  input_tokens: number;
  output_tokens: number;
  cache_creation_5m_tokens: number;
  cache_creation_1h_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
}

export interface ModelUsageInfo {
  input_tokens: number;
  output_tokens: number;
  cache_creation_5m_input_tokens: number;
  cache_creation_1h_input_tokens: number;
  cache_read_input_tokens: number;
  cost_usd: string;
}

export type DoneStatus = 'success' | 'error' | 'cancelled';

export interface DoneEventData extends BaseEventData {
  status: DoneStatus;
  result: string | null;
  is_error: boolean;
  errors: string[] | null;
  usage: UsageInfo;
  cost_usd: string;
  turn_count: number;
  duration_ms: number;
  session_id?: string;
  messages?: unknown[];
  model_usage?: Record<string, ModelUsageInfo>;
}

export interface DoneEvent {
  event: 'done';
  data: DoneEventData;
}

// ==========================================
// error イベント
// ==========================================

export type ErrorType =
  | 'conversation_locked'
  | 'sdk_not_installed'
  | 'model_validation_error'
  | 'options_error'
  | 'execution_error'
  | 'background_execution_error'
  | 'background_task_error'
  | 'timeout_error';

export interface ErrorEventData extends BaseEventData {
  error_type: ErrorType;
  message: string;
  recoverable: boolean;
}

export interface ErrorEvent {
  event: 'error';
  data: ErrorEventData;
}

// ==========================================
// 統合型
// ==========================================

export type StreamingEvent =
  | InitEvent
  | ThinkingEvent
  | AssistantEvent
  | ToolCallEvent
  | ToolResultEvent
  | SubagentStartEvent
  | SubagentEndEvent
  | ProgressEvent
  | TitleEvent
  | PingEvent
  | DoneEvent
  | ErrorEvent;

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

export function isInitEvent(event: StreamingEvent): event is InitEvent {
  return event.event === 'init';
}

export function isThinkingEvent(event: StreamingEvent): event is ThinkingEvent {
  return event.event === 'thinking';
}

export function isAssistantEvent(event: StreamingEvent): event is AssistantEvent {
  return event.event === 'assistant';
}

export function isToolCallEvent(event: StreamingEvent): event is ToolCallEvent {
  return event.event === 'tool_call';
}

export function isToolResultEvent(event: StreamingEvent): event is ToolResultEvent {
  return event.event === 'tool_result';
}

export function isSubagentStartEvent(event: StreamingEvent): event is SubagentStartEvent {
  return event.event === 'subagent_start';
}

export function isSubagentEndEvent(event: StreamingEvent): event is SubagentEndEvent {
  return event.event === 'subagent_end';
}

export function isProgressEvent(event: StreamingEvent): event is ProgressEvent {
  return event.event === 'progress';
}

export function isTitleEvent(event: StreamingEvent): event is TitleEvent {
  return event.event === 'title';
}

export function isPingEvent(event: StreamingEvent): event is PingEvent {
  return event.event === 'ping';
}

export function isDoneEvent(event: StreamingEvent): event is DoneEvent {
  return event.event === 'done';
}

export function isErrorEvent(event: StreamingEvent): event is ErrorEvent {
  return event.event === 'error';
}
```

## クライアント実装例

### Next.js (TypeScript)

```typescript
// hooks/useStreaming.ts

import { useState, useCallback, useRef } from 'react';
import type {
  StreamRequest,
  StreamingEvent,
  UsageInfo,
  DoneStatus,
  ToolCallEventData,
  ToolResultEventData,
} from '@/types/streaming';

interface StreamingState {
  isStreaming: boolean;
  sessionId: string | null;
  currentText: string;
  thinkingText: string;
  tools: string[];
  usage: UsageInfo | null;
  error: string | null;
  status: DoneStatus | null;
  lastSeq: number;
  pendingTools: Map<string, ToolCallEventData>;
}

interface StreamingHandlers {
  onInit?: (data: StreamingEvent['data']) => void;
  onThinking?: (content: string) => void;
  onAssistant?: (text: string) => void;
  onToolCall?: (data: ToolCallEventData) => void;
  onToolResult?: (data: ToolResultEventData) => void;
  onProgress?: (type: string, message: string) => void;
  onTitle?: (title: string) => void;
  onDone?: (data: StreamingEvent['data']) => void;
  onError?: (message: string, recoverable: boolean) => void;
}

export function useStreaming(tenantId: string, conversationId: string) {
  const [state, setState] = useState<StreamingState>({
    isStreaming: false,
    sessionId: null,
    currentText: '',
    thinkingText: '',
    tools: [],
    usage: null,
    error: null,
    status: null,
    lastSeq: 0,
    pendingTools: new Map(),
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
        thinkingText: '',
        status: null,
        lastSeq: 0,
        pendingTools: new Map(),
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
          handlers?.onError?.((error as Error).message, false);
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

    // シーケンス番号を更新
    if (data.seq > 0) {
      setState(prev => ({ ...prev, lastSeq: data.seq }));
    }

    switch (eventType) {
      case 'init':
        setState(prev => ({
          ...prev,
          sessionId: data.session_id,
          tools: data.tools,
        }));
        handlers?.onInit?.(data);
        break;

      case 'thinking':
        setState(prev => ({
          ...prev,
          thinkingText: prev.thinkingText + data.content,
        }));
        handlers?.onThinking?.(data.content);
        break;

      case 'assistant':
        for (const block of data.content_blocks) {
          if (block.type === 'text') {
            setState(prev => ({
              ...prev,
              currentText: prev.currentText + block.text,
            }));
            handlers?.onAssistant?.(block.text);
          }
        }
        break;

      case 'tool_call':
        setState(prev => {
          const newTools = new Map(prev.pendingTools);
          newTools.set(data.tool_use_id, data);
          return { ...prev, pendingTools: newTools };
        });
        handlers?.onToolCall?.(data);
        break;

      case 'tool_result':
        setState(prev => {
          const newTools = new Map(prev.pendingTools);
          newTools.delete(data.tool_use_id);
          return { ...prev, pendingTools: newTools };
        });
        handlers?.onToolResult?.(data);
        break;

      case 'progress':
        handlers?.onProgress?.(data.type, data.message);
        break;

      case 'title':
        handlers?.onTitle?.(data.title);
        break;

      case 'done':
        setState(prev => ({
          ...prev,
          usage: data.usage,
          status: data.status,
        }));
        handlers?.onDone?.(data);
        break;

      case 'error':
        setState(prev => ({
          ...prev,
          error: data.message,
        }));
        handlers?.onError?.(data.message, data.recoverable);
        break;

      case 'ping':
        // pingイベントは接続維持用なので特に処理しない
        break;
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
    thinkingText,
    usage,
    error,
    pendingTools,
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
      onTitle: (title) => {
        console.log('Title generated:', title);
      },
      onProgress: (type, message) => {
        console.log(`Progress [${type}]: ${message}`);
      },
    });

    setInput('');
  };

  return (
    <div>
      {/* 進捗表示 */}
      {isStreaming && pendingTools.size > 0 && (
        <div className="progress">
          ツール実行中: {pendingTools.size}
        </div>
      )}

      {/* 思考表示 */}
      {thinkingText && (
        <div className="thinking">
          <strong>思考中:</strong> {thinkingText}
        </div>
      )}

      {/* メッセージ表示 */}
      <div className="messages">
        {currentText && <div className="assistant">{currentText}</div>}
      </div>

      {/* エラー表示 */}
      {error && <div className="error">{error}</div>}

      {/* 使用量表示 */}
      {usage && (
        <div className="usage">
          Tokens: {usage.total_tokens}
        </div>
      )}

      {/* 入力 */}
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
