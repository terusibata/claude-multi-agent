# Claude Multi-Agent Mock API Server

claude-multi-agent プロジェクトのAPIと同じ形式で応答するモックサーバーです。
データベースを使用せず、メモリ内でデータを保持します。

## 起動方法

```bash
cd mock-api-server
npm install
npm start
```

開発モード（ファイル変更時に自動再起動）:
```bash
npm run dev
```

## 環境変数

| 変数名 | デフォルト値 | 説明 |
|--------|-------------|------|
| PORT | 3000 | サーバーのポート番号 |

## 認証

すべてのAPI（/health を除く）は認証が必要です。
以下のいずれかのヘッダーを設定してください（任意の値で認証可能）:

```
X-API-Key: any-value
```
または
```
Authorization: Bearer any-value
```

## 初期データ

### テナント
- `default-tenant` - デフォルトテナント

### モデル
- `global.anthropic.claude-sonnet4-5-20250929-v1:0` - Claude Sonnet 4.5

## エンドポイント一覧

### ヘルスチェック
- `GET /health` - 詳細ヘルスチェック
- `GET /health/live` - Liveness probe
- `GET /health/ready` - Readiness probe

### テナント管理
- `GET /api/tenants` - テナント一覧
- `POST /api/tenants` - テナント作成
- `GET /api/tenants/:tenant_id` - テナント詳細
- `PUT /api/tenants/:tenant_id` - テナント更新
- `DELETE /api/tenants/:tenant_id` - テナント削除

### モデル管理
- `GET /api/models` - モデル一覧
- `POST /api/models` - モデル作成
- `GET /api/models/:model_id` - モデル詳細
- `PUT /api/models/:model_id` - モデル更新
- `PATCH /api/models/:model_id/status` - ステータス変更
- `DELETE /api/models/:model_id` - モデル削除

### Skills管理
- `GET /api/tenants/:tenant_id/skills` - Skills一覧
- `POST /api/tenants/:tenant_id/skills` - Skill作成
- `GET /api/tenants/:tenant_id/skills/slash-commands` - スラッシュコマンド一覧
- `GET /api/tenants/:tenant_id/skills/:skill_id` - Skill詳細
- `PUT /api/tenants/:tenant_id/skills/:skill_id` - Skill更新
- `DELETE /api/tenants/:tenant_id/skills/:skill_id` - Skill削除

### MCPサーバー管理
- `GET /api/tenants/:tenant_id/mcp-servers` - MCPサーバー一覧
- `POST /api/tenants/:tenant_id/mcp-servers` - MCPサーバー作成
- `GET /api/tenants/:tenant_id/mcp-servers/builtin` - ビルトイン一覧
- `GET /api/tenants/:tenant_id/mcp-servers/:server_id` - MCPサーバー詳細
- `PUT /api/tenants/:tenant_id/mcp-servers/:server_id` - MCPサーバー更新
- `DELETE /api/tenants/:tenant_id/mcp-servers/:server_id` - MCPサーバー削除

### 会話管理
- `GET /api/tenants/:tenant_id/conversations` - 会話一覧
- `POST /api/tenants/:tenant_id/conversations` - 会話作成
- `GET /api/tenants/:tenant_id/conversations/:conversation_id` - 会話詳細
- `PUT /api/tenants/:tenant_id/conversations/:conversation_id` - 会話更新
- `POST /api/tenants/:tenant_id/conversations/:conversation_id/archive` - アーカイブ
- `DELETE /api/tenants/:tenant_id/conversations/:conversation_id` - 会話削除
- `GET /api/tenants/:tenant_id/conversations/:conversation_id/messages` - メッセージ一覧
- `POST /api/tenants/:tenant_id/conversations/:conversation_id/stream` - **ストリーミング実行**

### 使用状況・コスト
- `GET /api/tenants/:tenant_id/usage` - 使用状況一覧
- `GET /api/tenants/:tenant_id/usage/users/:user_id` - ユーザー別使用状況
- `GET /api/tenants/:tenant_id/usage/summary` - 使用状況サマリー
- `GET /api/tenants/:tenant_id/cost-report` - コストレポート
- `GET /api/tenants/:tenant_id/tool-logs` - ツール実行ログ

### ワークスペース
- `GET /api/tenants/:tenant_id/conversations/:conversation_id/files` - ファイル一覧
- `GET /api/tenants/:tenant_id/conversations/:conversation_id/files/download` - ファイルダウンロード
- `GET /api/tenants/:tenant_id/conversations/:conversation_id/files/presented` - AIが作成したファイル一覧

### シンプルチャット
- `GET /api/tenants/:tenant_id/simple-chats` - チャット一覧
- `GET /api/tenants/:tenant_id/simple-chats/:chat_id` - チャット詳細
- `POST /api/tenants/:tenant_id/simple-chats/:chat_id/archive` - アーカイブ
- `DELETE /api/tenants/:tenant_id/simple-chats/:chat_id` - チャット削除
- `POST /api/tenants/:tenant_id/simple-chats/stream` - **ストリーミング実行**

## ストリーミングエンドポイント

### 会話ストリーミング

```bash
curl -X POST "http://localhost:3000/api/tenants/default-tenant/conversations/{conversation_id}/stream" \
  -H "X-API-Key: test" \
  -H "Content-Type: multipart/form-data" \
  -F 'request_data={"user_input": "Hello", "executor": {"user_id": "user1", "name": "Test User", "email": "test@example.com"}}'
```

**レスポンス（Server-Sent Events）:**
- `session_start` - セッション開始
- `thinking` - 思考中（ランダム）
- `text_delta` - テキスト差分
- `tool_use` - ツール使用（50%の確率で`mcp__file-presentation__present_files`）
- `tool_result` - ツール結果
- `result` - 最終結果（usage, cost含む）

### シンプルチャットストリーミング

新規チャット:
```bash
curl -X POST "http://localhost:3000/api/tenants/default-tenant/simple-chats/stream" \
  -H "X-API-Key: test" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user1",
    "application_type": "test-app",
    "system_prompt": "You are a helpful assistant.",
    "model_id": "global.anthropic.claude-sonnet4-5-20250929-v1:0",
    "message": "Hello"
  }'
```

既存チャット:
```bash
curl -X POST "http://localhost:3000/api/tenants/default-tenant/simple-chats/stream" \
  -H "X-API-Key: test" \
  -H "Content-Type: application/json" \
  -d '{
    "chat_id": "{chat_id}",
    "message": "Hello again"
  }'
```

**レスポンス（Server-Sent Events）:**
- `text_delta` - テキスト差分
- `done` - 完了（usage, cost含む）

## ランダム性

モックサーバーは以下のランダム性を持ちます:

1. **ストリーミングテキスト**: ランダムなフレーズの組み合わせ
2. **ツール使用**: 50%の確率で`mcp__file-presentation__present_files`を使用
3. **ファイル生成**: ランダムなファイル名（report.pdf, analysis.xlsx等）
4. **使用量**: ランダムなトークン数
5. **応答遅延**: 50-150msのランダム遅延

## 制限事項

- データはメモリ内に保持され、サーバー再起動時にリセットされます
- ファイルアップロードは受け付けますが、実際には保存されません
- ダウンロードファイルはモックデータです
