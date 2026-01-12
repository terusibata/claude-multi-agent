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
POST /api/tenants/{tenant_id}/execute
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
    "chat_session_id": "chat-session-uuid",
    "tools": ["Read", "Write", "Bash", "Glob", "Grep"],
    "model": "Claude Sonnet 4",
    "agent_config": {
      "agent_config_id": "default-agent",
      "name": "デフォルトエージェント",
      "system_prompt": "...",
      "allowed_tools": ["Read", "Write"],
      "permission_mode": "default",
      "mcp_servers": [],
      "agent_skills": []
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

```
Client                          Server
  |                               |
  |  POST /execute                |
  |------------------------------>|
  |                               |
  |  event: message               |
  |  data: {type: "system", subtype: "init", ...}
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "assistant", content_blocks: [{type: "text", ...}]}
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "assistant", content_blocks: [{type: "tool_use", ...}]}
  |<------------------------------|
  |                               |
  |  event: message               |
  |  data: {type: "user_result", content_blocks: [{type: "tool_result", ...}]}
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

export interface AgentConfigInfo {
  agent_config_id: string;
  name: string;
  system_prompt: string | null;
  allowed_tools: string[];
  permission_mode: string;
  mcp_servers: string[];
  agent_skills: string[];
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
  chat_session_id?: string;
  tools: string[];
  model: string;
  agent_config?: AgentConfigInfo;
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

export type StreamingEvent = MessageEvent | ErrorEvent | TitleGeneratedEvent;

// ==========================================
// リクエスト型
// ==========================================

export interface ExecutorInfo {
  user_id: string;
  name: string;
  email: string;
  employee_id?: string;
}

export interface ExecuteRequest {
  agent_config_id: string;
  model_id: string;
  chat_session_id?: string;
  user_input: string;
  executor: ExecutorInfo;
  tokens?: Record<string, string>;
  resume_session_id?: string;
  fork_session?: boolean;
  enable_workspace?: boolean;
}

// ==========================================
// ユーティリティ型
// ==========================================

export type MessageHandler<T extends StreamingMessage['type']> = (
  message: Extract<StreamingMessage, { type: T }>
) => void;

export interface StreamingHandlers {
  onSystem?: MessageHandler<'system'>;
  onAssistant?: MessageHandler<'assistant'>;
  onUserResult?: MessageHandler<'user_result'>;
  onResult?: MessageHandler<'result'>;
  onError?: (error: ErrorEvent['data']) => void;
  onTitleGenerated?: (title: string) => void;
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
  ExecuteRequest,
  StreamingMessage,
  StreamingHandlers,
  ContentBlock,
  UsageInfo,
} from '@/types/streaming';

interface StreamingState {
  isStreaming: boolean;
  sessionId: string | null;
  messages: StreamingMessage[];
  currentText: string;
  tools: string[];
  usage: UsageInfo | null;
  error: string | null;
}

export function useStreaming(tenantId: string) {
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
    async (request: ExecuteRequest, handlers?: StreamingHandlers) => {
      // 既存のリクエストをキャンセル
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
        const response = await fetch(
          `/api/tenants/${tenantId}/execute`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify(request),
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

          // SSEパースの処理
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
              // イベント処理
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
    [tenantId]
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
          // テキストを累積
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

export function Chat() {
  const [input, setInput] = useState('');
  const {
    isStreaming,
    currentText,
    tools,
    usage,
    error,
    execute,
    cancel,
  } = useStreaming('tenant-001');

  const handleSubmit = async () => {
    if (!input.trim() || isStreaming) return;

    await execute({
      agent_config_id: 'default-agent',
      model_id: 'claude-sonnet-4',
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
          Tokens: {usage.total_tokens} | Cost: ${usage.total_cost_usd?.toFixed(4)}
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
