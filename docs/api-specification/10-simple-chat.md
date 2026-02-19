# シンプルチャットAPI

Claude Agent SDKを使わず、AWS Bedrock Converse APIを直接呼び出すシンプルなチャット機能を提供するAPIです。

## 概要

シンプルチャットは以下の特徴を持ちます：

- **SDKを使わない直接API呼び出し**: AWS Bedrock Converse APIを直接使用
- **テキストのみのチャット**: 添付ファイルやツール実行なし
- **ストリーミング応答**: SSE形式でリアルタイム応答
- **会話の継続**: 過去のやり取りを引き継いで継続可能
- **タイトル自動生成**: 初回応答完了時に自動生成
- **用途識別**: `application_type`による用途の記録

### ユースケース

- 翻訳アプリ (`translationApp`)
- 要約ツール (`summarizer`)
- 汎用チャットボット (`chatbot`)
- その他のシンプルなAI対話

---

## エンドポイント一覧

| メソッド | エンドポイント | 説明 |
|---------|---------------|------|
| `POST` | `/api/tenants/{tenant_id}/simple-chats/stream` | ストリーミング実行（新規・継続） |
| `GET` | `/api/tenants/{tenant_id}/simple-chats` | チャット一覧取得 |
| `GET` | `/api/tenants/{tenant_id}/simple-chats/{chat_id}` | チャット詳細取得 |
| `POST` | `/api/tenants/{tenant_id}/simple-chats/{chat_id}/archive` | チャットアーカイブ |
| `DELETE` | `/api/tenants/{tenant_id}/simple-chats/{chat_id}` | チャット削除 |

---

## POST /simple-chats/stream

チャットのストリーミング実行を行います。**1つのエンドポイントで新規作成と継続の両方を処理します。**

| 項目 | 値 |
|------|-----|
| エンドポイント | `POST /api/tenants/{tenant_id}/simple-chats/stream` |
| 認証 | 必要 |
| Content-Type | `application/json` |
| レスポンス形式 | `text/event-stream` (Server-Sent Events) |

### 動作モード

| モード | 条件 | 必須パラメータ |
|--------|------|---------------|
| **新規作成** | `chat_id` を指定しない | `user_id`, `application_type`, `system_prompt`, `model_id`, `message` |
| **継続** | `chat_id` を指定する | `chat_id`, `message` |

### リクエストボディ

```typescript
interface SimpleChatStreamRequest {
  // 継続時に指定（省略で新規作成）
  chat_id?: string;

  // 新規作成時に必須
  user_id?: string;
  application_type?: string;  // 例: "translationApp", "summarizer", "chatbot"
  system_prompt?: string;
  model_id?: string;

  // 常に必須
  message: string;
}
```

### リクエスト例

#### 新規作成

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/simple-chats/stream" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "application_type": "translationApp",
    "system_prompt": "You are a professional translator. Translate the following text to Japanese.",
    "model_id": "claude-sonnet-4",
    "message": "Hello, how are you?"
  }'
```

#### 継続

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/simple-chats/stream" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "chat_id": "550e8400-e29b-41d4-a716-446655440000",
    "message": "Now translate it to French."
  }'
```

### レスポンスヘッダー

| ヘッダー | 説明 | 条件 |
|---------|------|------|
| `X-Chat-ID` | 作成されたチャットID | 新規作成時のみ |

### SSEイベント形式

#### 共通構造

```
event: <event_type>
data: {"seq": <number>, "timestamp": "<ISO8601>", "event_type": "<type>", ...}

```

#### イベントタイプ

| イベント | 説明 | 発生タイミング |
|---------|------|---------------|
| `text_delta` | テキスト増分 | AI応答生成中 |
| `done` | 完了 | 応答完了時 |
| `error` | エラー | エラー発生時 |

### イベント詳細

#### text_delta（テキスト増分）

AI応答のテキストが増分で送信されます。

```json
{
  "seq": 1,
  "timestamp": "2024-01-15T10:30:00.123Z",
  "event_type": "text_delta",
  "content": "こんにちは"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `seq` | number | シーケンス番号 |
| `timestamp` | string | イベント発生時刻 |
| `event_type` | string | `"text_delta"` |
| `content` | string | テキスト増分 |

#### done（完了）

応答が完了したときに送信されます。

```json
{
  "seq": 10,
  "timestamp": "2024-01-15T10:30:02.456Z",
  "event_type": "done",
  "title": "挨拶の翻訳",
  "usage": {
    "input_tokens": 50,
    "output_tokens": 15,
    "total_tokens": 65
  },
  "cost_usd": "0.00013"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `seq` | number | シーケンス番号 |
| `timestamp` | string | イベント発生時刻 |
| `event_type` | string | `"done"` |
| `title` | string \| null | 自動生成されたタイトル（初回のみ） |
| `usage` | object | トークン使用情報 |
| `cost_usd` | string | コスト（USD）※Decimal値を文字列でシリアライズ |

#### error（エラー）

エラーが発生したときに送信されます。

```json
{
  "seq": 99,
  "timestamp": "2024-01-15T10:30:03.789Z",
  "event_type": "error",
  "message": "モデルからの応答がタイムアウトしました",
  "error_type": "TimeoutError",
  "recoverable": false
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `seq` | number | シーケンス番号 |
| `timestamp` | string | イベント発生時刻 |
| `event_type` | string | `"error"` |
| `message` | string | エラーメッセージ |
| `error_type` | string | エラータイプ |
| `recoverable` | boolean | 復旧可能かどうか |

### フロントエンド実装例

```typescript
const response = await fetch(
  `/api/tenants/${tenantId}/simple-chats/stream`,
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey,
    },
    body: JSON.stringify({
      user_id: userId,
      application_type: 'translationApp',
      system_prompt: 'You are a translator.',
      model_id: 'claude-sonnet-4',
      message: userInput,
    }),
  }
);

// 新規作成時はヘッダーからchat_idを取得
const chatId = response.headers.get('X-Chat-ID');
if (chatId) {
  saveChatId(chatId);  // 継続用に保存
}

// SSEを処理
const reader = response.body.getReader();
const decoder = new TextDecoder();
let fullText = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const chunk = decoder.decode(value);
  const lines = chunk.split('\n');

  for (const line of lines) {
    if (line.startsWith('data: ')) {
      const data = JSON.parse(line.slice(6));

      switch (data.event_type) {
        case 'text_delta':
          fullText += data.content;
          updateUI(fullText);
          break;
        case 'done':
          if (data.title) {
            updateChatTitle(data.title);
          }
          showUsage(data.usage, data.cost_usd);
          break;
        case 'error':
          showError(data.message);
          break;
      }
    }
  }
}
```

---

## GET /simple-chats

チャット一覧を取得します。

### クエリパラメータ

| パラメータ | 型 | 必須 | 説明 | デフォルト |
|-----------|-----|------|------|-----------|
| `user_id` | string | No | ユーザーIDでフィルタ | - |
| `application_type` | string | No | アプリケーションタイプでフィルタ | - |
| `status` | string | No | ステータスでフィルタ（active/archived） | - |
| `limit` | int | No | 取得件数 | 50 |
| `offset` | int | No | オフセット | 0 |

### リクエスト例

```bash
curl "https://api.example.com/api/tenants/acme-corp/simple-chats?user_id=user-001&application_type=translationApp&limit=20" \
  -H "X-API-Key: your_api_key"
```

### レスポンス

```json
{
  "items": [
    {
      "chat_id": "550e8400-e29b-41d4-a716-446655440000",
      "tenant_id": "acme-corp",
      "user_id": "user-001",
      "model_id": "claude-sonnet-4",
      "application_type": "translationApp",
      "system_prompt": "You are a professional translator...",
      "title": "挨拶の翻訳",
      "status": "active",
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:02Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

## GET /simple-chats/{chat_id}

チャット詳細（メッセージ履歴含む）を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `chat_id` | string | Yes | チャットID（UUID） |

### リクエスト例

```bash
curl "https://api.example.com/api/tenants/acme-corp/simple-chats/550e8400-e29b-41d4-a716-446655440000" \
  -H "X-API-Key: your_api_key"
```

### レスポンス

```json
{
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "acme-corp",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "application_type": "translationApp",
  "system_prompt": "You are a professional translator...",
  "title": "挨拶の翻訳",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:02Z",
  "messages": [
    {
      "message_id": "661f8511-f30c-52e5-b827-557766551111",
      "chat_id": "550e8400-e29b-41d4-a716-446655440000",
      "message_seq": 1,
      "role": "user",
      "content": "Hello, how are you?",
      "created_at": "2024-01-15T10:30:00Z"
    },
    {
      "message_id": "772f8622-g41d-63f6-c938-668877662222",
      "chat_id": "550e8400-e29b-41d4-a716-446655440000",
      "message_seq": 2,
      "role": "assistant",
      "content": "こんにちは、お元気ですか？",
      "created_at": "2024-01-15T10:30:02Z"
    }
  ]
}
```

---

## POST /simple-chats/{chat_id}/archive

チャットをアーカイブします。アーカイブされたチャットは継続メッセージを送信できなくなりますが、履歴の参照は引き続き可能です。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `chat_id` | string | Yes | チャットID（UUID） |

### リクエスト例

```bash
curl -X POST "https://api.example.com/api/tenants/acme-corp/simple-chats/550e8400-e29b-41d4-a716-446655440000/archive" \
  -H "X-API-Key: your_api_key"
```

### レスポンス

```json
{
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "acme-corp",
  "user_id": "user-001",
  "model_id": "claude-sonnet-4",
  "application_type": "translationApp",
  "system_prompt": "You are a professional translator...",
  "title": "挨拶の翻訳",
  "status": "archived",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T11:00:00Z"
}
```

---

## DELETE /simple-chats/{chat_id}

チャットを削除します。関連するメッセージも一緒に削除されます。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `chat_id` | string | Yes | チャットID（UUID） |

### リクエスト例

```bash
curl -X DELETE "https://api.example.com/api/tenants/acme-corp/simple-chats/550e8400-e29b-41d4-a716-446655440000" \
  -H "X-API-Key: your_api_key"
```

### レスポンス

HTTPステータスコード `204 No Content`（ボディなし）

---

## エラーレスポンス

### 一般的なエラー

| HTTPステータス | エラー | 説明 |
|---------------|-------|------|
| 400 | Bad Request | リクエストパラメータが不正 |
| 404 | Not Found | テナントまたはチャットが見つからない |
| 500 | Internal Server Error | サーバー内部エラー |

### エラーレスポンス例

```json
{
  "detail": "新規作成時は user_id が必須です"
}
```

```json
{
  "detail": "チャット '550e8400-...' はアーカイブされています"
}
```

---

## データモデル

### SimpleChat

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `chat_id` | string (UUID) | チャットID |
| `tenant_id` | string | テナントID |
| `user_id` | string | ユーザーID |
| `model_id` | string | 使用モデルID |
| `application_type` | string | アプリケーションタイプ |
| `system_prompt` | string | システムプロンプト |
| `title` | string \| null | タイトル（自動生成） |
| `status` | string | ステータス（active/archived） |
| `created_at` | string (ISO 8601) | 作成日時 |
| `updated_at` | string (ISO 8601) | 更新日時 |

### SimpleChatMessage

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `message_id` | string (UUID) | メッセージID |
| `chat_id` | string (UUID) | チャットID |
| `message_seq` | number | メッセージ順序 |
| `role` | string | ロール（user/assistant） |
| `content` | string | メッセージ内容 |
| `created_at` | string (ISO 8601) | 作成日時 |

---

## 使用量の記録

シンプルチャットの使用量は、エージェント実行と同じ `usage_logs` テーブルに記録されます。

| フィールド | 説明 |
|-----------|------|
| `tenant_id` | テナントID |
| `user_id` | ユーザーID |
| `model_id` | 使用モデルID |
| `simple_chat_id` | シンプルチャットID（エージェント実行の場合は `conversation_id`） |
| `input_tokens` | 入力トークン数 |
| `output_tokens` | 出力トークン数 |
| `cost_usd` | コスト（USD） |

これにより、エージェント実行とシンプルチャットの使用量を統一的に分析・レポートできます。
