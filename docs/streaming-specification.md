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
- **ハートビート間隔**: 15秒
- **再接続間隔**: 3秒（SSE `retry` フィールドで指定）

### SSE イベントフォーマット

各イベントには以下のフィールドが含まれます：

```
id: {conversation_id}:{sequence}
event: {event_type}
data: {JSON_data}
retry: 3000
```

| フィールド | 説明 |
|-----------|------|
| `id` | イベントの一意識別子（`{conversation_id}:{sequence}` 形式） |
| `event` | イベントタイプ |
| `data` | JSON形式のイベントデータ |
| `retry` | 再接続間隔（ミリ秒、初回のみ） |

## イベントタイプ

### 1. connection_init イベント

接続初期化イベント。SSE接続確立時に最初に送信されます。

```json
{
  "status": "connected",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### 2. heartbeat イベント

ハートビートイベント。15秒間隔で送信され、接続維持とクライアントへの生存確認に使用されます。

```json
{
  "status": "processing",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### 3. text_delta イベント（リアルタイムストリーミング）

テキストの増分をリアルタイムで配信します。トークンレベルのストリーミングを実現。

```json
{
  "type": "text_delta",
  "index": 0,
  "text": "こんにちは",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### 4. thinking_delta イベント（リアルタイムストリーミング）

Extended Thinkingの思考内容をリアルタイムで配信します。

```json
{
  "type": "thinking_delta",
  "index": 0,
  "thinking": "ユーザーの質問を分析しています...",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### 5. content_block_start イベント

コンテンツブロックの開始を通知します。

```json
{
  "type": "content_block_start",
  "index": 0,
  "content_block": {
    "type": "text",
    "text": ""
  },
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### 6. content_block_stop イベント

コンテンツブロックの終了を通知します。

```json
{
  "type": "content_block_stop",
  "index": 0,
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

### 7. message イベント

メインのメッセージイベント。`type` フィールドで種類を区別します。

| type | 説明 |
|------|------|
| `system` | システムメッセージ（初期化など） |
| `assistant` | アシスタントからのメッセージ（テキスト、ツール使用、思考） |
| `user_result` | ツール実行結果 |
| `result` | 最終結果 |

### 8. error イベント

エラー発生時に送信されます。

### 9. title_generated イベント

初回実行時、タイトルが自動生成された際に送信されます。

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

最終結果メッセージ。

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
    "cache_creation_tokens": 0,
    "cache_read_tokens": 200,
    "total_tokens": 2000
  },
  "total_cost_usd": 0.0075,
  "num_turns": 3,
  "duration_ms": 5230,
  "session_id": "session-uuid-from-sdk"
}
```

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

## フロー図

### リアルタイムストリーミングフロー（推奨）

```
Client                                    Server
  |                                         |
  |  POST /conversations/{id}/stream        |
  |---------------------------------------->|
  |                                         |
  |  id: conv-123:1                         |
  |  event: connection_init                 |
  |  data: {status: "connected"}            |
  |  retry: 3000                            |
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:2                         |
  |  event: message                         |
  |  data: {type: "system", subtype: "init"}|
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:3                         |
  |  event: content_block_start             |
  |  data: {index: 0, content_block: {type: "text"}}
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:4                         |
  |  event: text_delta                      |  ← リアルタイム
  |  data: {index: 0, text: "こんにちは"}   |    テキスト配信
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:5                         |
  |  event: text_delta                      |
  |  data: {index: 0, text: "！"}           |
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:6                         |
  |  event: content_block_stop              |
  |  data: {index: 0}                       |
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:7                         |
  |  event: message                         |  ← 完成した
  |  data: {type: "assistant", ...}         |    メッセージ
  |<----------------------------------------|
  |                                         |
  |  [ツール実行中、15秒経過]               |
  |                                         |
  |  id: conv-123:8                         |
  |  event: heartbeat                       |  ← ハートビート
  |  data: {status: "processing"}           |
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:9                         |
  |  event: message                         |
  |  data: {type: "user_result", ...}       |
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:10                        |
  |  event: title_generated                 |
  |  data: {title: "..."}                   |
  |<----------------------------------------|
  |                                         |
  |  id: conv-123:11                        |
  |  event: message                         |
  |  data: {type: "result", subtype: "success"}
  |<----------------------------------------|
  |                                         |
  |  (connection closed)                    |
  |<----------------------------------------|
```

### イベントID と Last-Event-ID

クライアントが接続を再確立する際、`Last-Event-ID` ヘッダーを送信することで、
欠落したイベントを特定できます（将来の実装で対応予定）。

```
id: {conversation_id}:{sequence}
```

例: `id: conv-abc123:42`

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

export interface UsageInfo {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
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
}

export type StreamingMessage =
  | SystemMessage
  | AssistantMessage
  | UserResultMessage
  | ResultMessage;

// ==========================================
// イベント型
// ==========================================

// 接続初期化イベント
export interface ConnectionInitEvent {
  event: 'connection_init';
  data: {
    status: 'connected';
    timestamp: string;
  };
}

// ハートビートイベント
export interface HeartbeatEvent {
  event: 'heartbeat';
  data: {
    status: 'processing' | 'idle';
    timestamp: string;
  };
}

// テキストデルタイベント（リアルタイムストリーミング）
export interface TextDeltaEvent {
  event: 'text_delta';
  data: {
    type: 'text_delta';
    index: number;
    text: string;
    timestamp: string;
  };
}

// 思考デルタイベント（リアルタイムストリーミング）
export interface ThinkingDeltaEvent {
  event: 'thinking_delta';
  data: {
    type: 'thinking_delta';
    index: number;
    thinking: string;
    timestamp: string;
  };
}

// コンテンツブロック開始イベント
export interface ContentBlockStartEvent {
  event: 'content_block_start';
  data: {
    type: 'content_block_start';
    index: number;
    content_block: {
      type: string;
      text?: string;
      id?: string;
      name?: string;
    };
    timestamp: string;
  };
}

// コンテンツブロック終了イベント
export interface ContentBlockStopEvent {
  event: 'content_block_stop';
  data: {
    type: 'content_block_stop';
    index: number;
    timestamp: string;
  };
}

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

// 全イベント型
export type StreamingEvent =
  | ConnectionInitEvent
  | HeartbeatEvent
  | TextDeltaEvent
  | ThinkingDeltaEvent
  | ContentBlockStartEvent
  | ContentBlockStopEvent
  | MessageEvent
  | ErrorEvent
  | TitleGeneratedEvent;

// SSEイベントのラッパー型（イベントIDを含む）
export interface SSEEvent {
  id: string;           // イベントID（例: "conv-123:42"）
  event: string;        // イベントタイプ
  data: string;         // JSON文字列
  retry?: number;       // 再接続間隔（ミリ秒）
}

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

// イベント型ガード
export function isConnectionInitEvent(event: StreamingEvent): event is ConnectionInitEvent {
  return event.event === 'connection_init';
}

export function isHeartbeatEvent(event: StreamingEvent): event is HeartbeatEvent {
  return event.event === 'heartbeat';
}

export function isTextDeltaEvent(event: StreamingEvent): event is TextDeltaEvent {
  return event.event === 'text_delta';
}

export function isThinkingDeltaEvent(event: StreamingEvent): event is ThinkingDeltaEvent {
  return event.event === 'thinking_delta';
}

export function isContentBlockStartEvent(event: StreamingEvent): event is ContentBlockStartEvent {
  return event.event === 'content_block_start';
}

export function isContentBlockStopEvent(event: StreamingEvent): event is ContentBlockStopEvent {
  return event.event === 'content_block_stop';
}

export function isMessageEvent(event: StreamingEvent): event is MessageEvent {
  return event.event === 'message';
}

export function isErrorEvent(event: StreamingEvent): event is ErrorEvent {
  return event.event === 'error';
}

export function isTitleGeneratedEvent(event: StreamingEvent): event is TitleGeneratedEvent {
  return event.event === 'title_generated';
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
  StreamingEvent,
  ContentBlock,
  UsageInfo,
} from '@/types/streaming';

interface StreamingHandlers {
  onConnectionInit?: () => void;
  onHeartbeat?: (status: string) => void;
  onTextDelta?: (text: string, index: number) => void;
  onThinkingDelta?: (thinking: string, index: number) => void;
  onContentBlockStart?: (index: number, block: { type: string }) => void;
  onContentBlockStop?: (index: number) => void;
  onSystem?: (message: StreamingMessage) => void;
  onAssistant?: (message: StreamingMessage) => void;
  onUserResult?: (message: StreamingMessage) => void;
  onResult?: (message: StreamingMessage) => void;
  onError?: (error: { message: string }) => void;
  onTitleGenerated?: (title: string) => void;
}

interface StreamingState {
  isStreaming: boolean;
  isConnected: boolean;
  sessionId: string | null;
  messages: StreamingMessage[];
  currentText: string;
  streamingText: string;  // リアルタイムストリーミング用
  tools: string[];
  usage: UsageInfo | null;
  error: string | null;
  lastEventId: string | null;
}

export function useStreaming(tenantId: string, conversationId: string) {
  const [state, setState] = useState<StreamingState>({
    isStreaming: false,
    isConnected: false,
    sessionId: null,
    messages: [],
    currentText: '',
    streamingText: '',
    tools: [],
    usage: null,
    error: null,
    lastEventId: null,
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
        isConnected: false,
        error: null,
        currentText: '',
        streamingText: '',
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

          let eventId = '';
          let eventType = '';
          let eventData = '';

          for (const line of lines) {
            if (line.startsWith('id:')) {
              eventId = line.slice(3).trim();
            } else if (line.startsWith('event:')) {
              eventType = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              eventData = line.slice(5).trim();
            } else if (line === '' && eventType && eventData) {
              // イベントIDを保存
              if (eventId) {
                setState(prev => ({ ...prev, lastEventId: eventId }));
              }
              processEvent(eventType, eventData, handlers, setState);
              eventId = '';
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
        setState(prev => ({ ...prev, isStreaming: false, isConnected: false }));
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

    switch (eventType) {
      // 接続初期化イベント
      case 'connection_init':
        setState(prev => ({ ...prev, isConnected: true }));
        handlers?.onConnectionInit?.();
        break;

      // ハートビートイベント
      case 'heartbeat':
        handlers?.onHeartbeat?.(data.status);
        break;

      // テキストデルタイベント（リアルタイムストリーミング）
      case 'text_delta':
        setState(prev => ({
          ...prev,
          streamingText: prev.streamingText + data.text,
        }));
        handlers?.onTextDelta?.(data.text, data.index);
        break;

      // 思考デルタイベント
      case 'thinking_delta':
        handlers?.onThinkingDelta?.(data.thinking, data.index);
        break;

      // コンテンツブロック開始
      case 'content_block_start':
        handlers?.onContentBlockStart?.(data.index, data.content_block);
        break;

      // コンテンツブロック終了
      case 'content_block_stop':
        handlers?.onContentBlockStop?.(data.index);
        break;

      // メッセージイベント
      case 'message': {
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
            // AssistantMessage受信時にstreamingTextをcurrentTextに反映
            setState(prev => {
              let newCurrentText = prev.currentText;
              for (const block of message.content_blocks) {
                if (block.type === 'text') {
                  newCurrentText += block.text;
                }
              }
              return {
                ...prev,
                currentText: newCurrentText,
                streamingText: '',  // リセット
              };
            });
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
        break;
      }

      // エラーイベント
      case 'error':
        setState(prev => ({
          ...prev,
          error: data.message,
        }));
        handlers?.onError?.(data);
        break;

      // タイトル生成イベント
      case 'title_generated':
        handlers?.onTitleGenerated?.(data.title);
        break;

      default:
        console.log('Unknown event type:', eventType, data);
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
