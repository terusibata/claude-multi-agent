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
    ├─ OptionsBuilder で ClaudeAgentOptions 構築
    │   ├─ system_prompt (テナント設定)
    │   ├─ model (Bedrock model ID)
    │   ├─ allowed_tools: ["*"]
    │   ├─ mcp_servers (テナント設定)
    │   ├─ permission_mode: "bypassPermissions"
    │   └─ cwd (ワークスペースパス)
    │
    ▼
Claude Agent SDK (ClaudeSDKClient)
    │
    ├─ Claude Code CLI をサブプロセスとして起動
    │   └─ stdin/stdout JSON プロトコルで通信
    │
    ├─ CLI 内部でツール実行ループ
    │   ├─ Bash: シェルコマンド実行
    │   ├─ Read/Write/Edit: ファイル操作
    │   ├─ Glob/Grep: ファイル検索
    │   └─ MCP Tools: カスタムツール
    │
    ▼
AsyncIterator[Message] → SSE Events → Frontend
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
  /var/lib/aiagent/workspaces/  (一時キャッシュ)
```

## 現在の問題点

| 問題 | リスク | 影響度 |
|------|--------|--------|
| 全セッションが同一プロセス空間で実行 | あるセッションの `rm -rf /` が全体に影響 | **Critical** |
| ファイルシステムが共有 | テナント間でファイルアクセス可能 | **Critical** |
| `pip install` が全セッションに影響 | バージョン競合、依存関係の汚染 | **High** |
| リソース制限なし | 1セッションが CPU/メモリを独占 | **High** |
| ネットワーク制限なし | コンテナから DB/Redis に直接アクセス可能 | **Medium** |
| CLI プロセスのライフサイクル管理なし | ゾンビプロセスの蓄積 | **Medium** |

## Claude Agent SDK の動作モデル

### 重要な特性

1. **サブプロセスモデル**: SDK は Claude Code CLI を子プロセスとして起動する（Node.js バイナリ）
2. **永続的シェル環境**: CLI は永続的な作業ディレクトリとシェル環境を維持する
3. **ツール実行は CLI 内部**: Bash、Read、Write 等のツールは CLI プロセス内で実行される
4. **MCP サーバーは in-process**: カスタムツールは Python プロセス内で動作する

### 隔離設計への示唆

- CLI が実際にファイルシステムとシェルを操作するため、**コンテナレベルの隔離が必須**
- `bypassPermissions` モードを使用中 → コンテナ境界が唯一のセキュリティ境界
- MCP サーバー（ファイルツール等）はバックエンド側で動作 → コンテナ内に移す必要がある
