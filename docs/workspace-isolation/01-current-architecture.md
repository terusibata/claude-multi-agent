# 01 - 現在のアーキテクチャ分析

## 現在の実行フロー

```
User Request (POST /stream)
    │
    ▼
FastAPI Backend (単一コンテナ)
    │
    ├─ 認証・レート制限
    ├─ ExecutionContext 構築
    ├─ Redis 分散ロック取得 (会話単位)
    │
    ▼
ExecuteService.execute_streaming()
    │
    ├─ OptionsBuilder で SDK Options 構築
    │   ├─ system_prompt (テナント設定)
    │   ├─ model (Bedrock model ID)
    │   ├─ allowed_tools: ["*"]
    │   ├─ mcp_servers (builtin + tenant設定)
    │   ├─ permission_mode: "bypassPermissions"
    │   ├─ cwd (ワークスペースパス)
    │   └─ env (AWS認証情報、モデルID)
    │
    ▼
Claude Agent SDK (In-Process Python ライブラリ)
    │
    ├─ async with ClaudeSDKClient(options) as client:
    │   ├─ client.query(user_input)
    │   └─ async for message in client.receive_response():
    │       ├─ SystemMessage (init: session_id, tools)
    │       ├─ AssistantMessage (text, thinking, tool_use)
    │       ├─ UserMessage (tool results)
    │       └─ ResultMessage (final, usage)
    │
    ├─ SDK 内部で CLI サブプロセスを起動（Node.js）
    │   ├─ Bash: シェルコマンド実行
    │   ├─ Read/Write/Edit: ファイル操作
    │   └─ Glob/Grep: ファイル検索
    │
    ├─ MCP サーバー
    │   ├─ builtin (in-process): file-tools, file-presentation
    │   ├─ stdio: SDK がサブプロセスとして起動
    │   └─ http/sse: ネットワーク経由
    │
    ▼
AsyncIterator[Message] → Queue → SSE Events → Frontend
```

## 現在のワークスペース実装

### データモデル

```
conversations テーブル:
  - workspace_enabled: bool
  - workspace_path: str  (S3パス: "workspaces/{tenant_id}/{conversation_id}/")
  - workspace_created_at: datetime

conversation_files テーブル:
  - file_path: str (相対パス)
  - source: "user_upload" | "ai_created" | "ai_modified"
  - version: int (同パスの世代管理)
  - checksum: str (SHA256)
```

### ストレージ構成

```
S3:
  workspaces/{tenant_id}/{conversation_id}/
    ├─ uploads/    (ユーザーアップロード)
    └─ outputs/    (AI生成ファイル)

ローカル:
  /var/lib/aiagent/workspaces/  (一時キャッシュ、リクエスト終了後に削除)
```

### SDK 実行時の環境変数

| 変数 | 用途 |
|------|------|
| `CLAUDE_CODE_USE_BEDROCK` | Bedrock プロバイダー有効化 |
| `AWS_REGION` | AWS リージョン |
| `AWS_ACCESS_KEY_ID` | AWS 認証 |
| `AWS_SECRET_ACCESS_KEY` | AWS 認証 |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Bedrock Sonnet モデルID |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Bedrock Haiku モデルID |
| `CLAUDE_CODE_SUBAGENT_MODEL` | サブエージェントモデル |

## 現在の問題点

| 問題 | リスク | 影響度 |
|------|--------|--------|
| 全セッションが同一プロセス空間で実行 | あるセッションの `rm -rf /` が全体に影響 | **Critical** |
| ファイルシステムが共有 | テナント間でファイルアクセス可能 | **Critical** |
| `pip install` が全セッションに影響 | バージョン競合、依存関係の汚染 | **High** |
| リソース制限なし | 1セッションが CPU/メモリを独占 | **High** |
| ネットワーク制限なし | コンテナから DB/Redis に直接アクセス可能 | **Medium** |
| AWS 認証情報が環境変数で全セッションに共有 | 認証情報の漏洩リスク | **Medium** |

## Claude Agent SDK の動作モデル

### 重要な特性（コードベース検証済み）

1. **In-Process ライブラリ**: SDK は Python ライブラリとして同一プロセス内で動作する
2. **Async Context Manager**: `async with ClaudeSDKClient(options) as client:` パターン
3. **ストリーミングレスポンス**: `async for message in client.receive_response()` で逐次受信
4. **内部 CLI サブプロセス**: SDK 内部で Claude Code CLI (Node.js) を起動し、ツール実行を委譲
5. **MCP サーバー分離**: builtin は in-process、stdio は SDK がサブプロセス起動、http/sse はネットワーク

### 隔離設計への示唆

- SDK + CLI が同一コンテナ内で動作する必要がある
- `bypassPermissions` モードのため、コンテナ境界が唯一のセキュリティ境界
- builtin MCP サーバー（file-tools, file-presentation）はコンテナ内の Python プロセスで動作させる
- AWS 認証情報はコンテナに直接渡さず、プロキシ経由で注入すべき（Anthropic 公式推奨）
