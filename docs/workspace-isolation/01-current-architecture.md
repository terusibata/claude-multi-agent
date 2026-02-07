# 1. 現行アーキテクチャ分析

## 1.1 システム概要

現在のシステムは以下のコンポーネントで構成される:

```
┌─────────────────────────────────────────────────────────────┐
│                    docker-compose.yml                        │
│                                                             │
│  ┌──────────┐   ┌──────────┐   ┌────────────────────────┐  │
│  │ postgres  │   │  redis   │   │       backend          │  │
│  │  :5432    │   │  :6379   │   │  (Python 3.11 + uvicorn│  │
│  │           │   │          │   │   + Node.js 20.x)      │  │
│  └──────────┘   └──────────┘   └────────────────────────┘  │
│                                         │                    │
│                                  ┌──────┴──────┐            │
│                                  │ /var/lib/    │            │
│                                  │ aiagent/     │            │
│                                  │ workspaces/  │            │
│                                  └─────────────┘            │
└─────────────────────────────────────────────────────────────┘
                                         │
                                    ┌────┴────┐
                                    │   S3    │
                                    │ Bucket  │
                                    └─────────┘
```

### コンテナ構成

| サービス | イメージ | 役割 |
|---------|---------|------|
| `db` | postgres:15-alpine | データベース |
| `redis` | redis:7-alpine | 分散ロック / レート制限 / キャッシュ |
| `backend` | 独自ビルド (Python 3.11-slim + Node.js 20.x) | APIサーバー + エージェント実行 |

### 重要な点

- **backend コンテナが API サーバーとエージェント実行の両方を兼ねている**
- Node.js は Claude Agent SDK のランタイムとして必要（SDK は Node.js ベース）
- ワークスペースは Docker Volume (`workspaces_data`) でマウントされている

## 1.2 エージェント実行フロー

```
クライアント
  │
  │ POST /execute (SSE)
  ▼
ExecuteService.execute_streaming()
  │
  ├── 1. コンテキスト制限チェック
  ├── 2. 会話ロック取得 (Redis分散ロック)
  ├── 3. OptionsBuilder.build()
  │      ├── cwd 決定
  │      │     ├── workspace_enabled=false → テナントcwd (/skills/{tenant_id})
  │      │     └── workspace_enabled=true  → S3→ローカル同期 → /var/lib/aiagent/workspaces/workspace_{conv_id}
  │      ├── MCPサーバー設定構築
  │      ├── システムプロンプト構築
  │      └── AWS環境変数構築
  │
  ├── 4. ClaudeSDKClient 実行 ← Node.js 子プロセスが起動
  │      │
  │      │  ┌──────────────────────────────────┐
  │      │  │  Node.js 子プロセス              │
  │      │  │  ┌───────────────────────────┐   │
  │      │  │  │ Claude Agent SDK          │   │
  │      │  │  │  - Anthropic API 呼出     │   │
  │      │  │  │  - ツール実行 (Bash等)    │   │
  │      │  │  │  - ファイル読み書き       │   │
  │      │  │  │  - サブエージェント生成   │   │
  │      │  │  └───────────────────────────┘   │
  │      │  └──────────────────────────────────┘
  │      │
  │      ├── async for message in client.receive_response():
  │      │     ├── SystemMessage  → セッション初期化
  │      │     ├── AssistantMessage → テキスト/ツール使用/思考
  │      │     ├── UserMessage    → ツール結果
  │      │     └── ResultMessage  → 完了 → ワークスペース同期
  │      │
  ├── 5. ワークスペース同期 (workspace_enabled=true の場合)
  │      ├── ローカル → S3 同期
  │      ├── AIファイル自動登録
  │      └── ローカルクリーンアップ
  │
  └── 6. 会話ロック解放 / DB commit or rollback
```

## 1.3 Claude Agent SDK の動作モデル

### プロセスモデル

```
Python (uvicorn) ─── stdio ──→ Node.js (Claude Agent SDK)
      │                              │
      │  ClaudeSDKClient             │  claude_agent_sdk (npm パッケージ)
      │  - query() → stdin           │  - Anthropic API 呼出
      │  - receive_response() ← stdout│  - ツール実行（Bash, Read, Write, etc.）
      │                              │  - サブエージェント（追加 Node.js プロセス）
      │                              │
      │  options:                    │
      │    cwd: 作業ディレクトリ      │  → プロセスの cwd として設定
      │    env: 環境変数              │  → プロセスの env として設定
      │    allowed_tools: ツール許可  │  → SDK内部でフィルタ
      │    mcp_servers: MCP設定       │  → SDK が MCP サーバープロセスを起動
      └──────────────────────────────┘
```

### 重要な技術的事実

1. **SDK は Node.js ベース**: Python の `claude_agent_sdk` は thin wrapper。実体は Node.js 子プロセス
2. **stdio 通信**: Python ↔ Node.js は標準入出力（stdin/stdout）で JSON メッセージをやり取り
3. **ツール実行は SDK 内部**: Bash コマンド実行、ファイル読み書きは Node.js プロセス内で行われる
4. **cwd が重要**: SDK に渡す `cwd` がエージェントの作業ディレクトリとなる
5. **MCP サーバーは別プロセス**: SDK が MCP サーバーを子プロセスとして起動・管理
6. **サブエージェントも別プロセス**: Task ツールで生成されるサブエージェントは追加のプロセス

### ツール実行のスコープ

Claude Agent SDK が実行可能なツール:

| ツール | 危険度 | 説明 |
|--------|-------|------|
| Bash | **極高** | 任意のシェルコマンド実行。`rm -rf /`, `curl`, `pip install` 等 |
| Write | 高 | 任意のパスへのファイル書き込み |
| Edit | 中 | 既存ファイルの編集 |
| Read | 低 | ファイル読み取り |
| Glob | 低 | ファイル検索 |
| Grep | 低 | コンテンツ検索 |
| Task | 高 | サブエージェント生成（Bash権限を持つ子プロセスを増殖） |

## 1.4 現行ワークスペースの仕組み

### ライフサイクル

```
1. リクエスト受信 (workspace_enabled=true)
     │
2. S3 → ローカル同期
     │  S3: workspaces/{tenant_id}/{conversation_id}/*
     │  → /var/lib/aiagent/workspaces/workspace_{conversation_id}/
     │
3. SDK 実行 (cwd = ローカルディレクトリ)
     │  エージェントがファイル作成・変更・コマンド実行
     │
4. ローカル → S3 同期
     │  変更されたファイルを S3 にアップロード
     │  AIファイルを conversation_files テーブルに登録
     │
5. ローカルクリーンアップ
     │  /var/lib/aiagent/workspaces/workspace_{conversation_id}/ を削除
```

### セキュリティ対策（現状）

| 対策 | 実装状況 | 備考 |
|------|---------|------|
| パストラバーサル防止 | 済 | `validate_path_traversal()`, `validate_conversation_id()` |
| 会話所有権チェック | 済 | テナントIDと会話IDの照合 |
| ファイルサイズ制限 | 済 | ファイルタイプ別のサイズ上限 |
| 非rootユーザー実行 | 済 | `appuser` (UID 1000) |
| ファイルシステム隔離 | **未対応** | コンテナ内で全会話が同一FS上 |
| プロセス隔離 | **未対応** | SDK プロセスは同一名前空間 |
| ネットワーク隔離 | **未対応** | SDK プロセスはホストネットワークにアクセス可能 |
| リソース制限 (per-session) | **未対応** | コンテナ全体での制限のみ（本番: 4CPU/8GB） |
| コマンド実行制限 | **未対応** | SDK の allowed_tools=["*"] で全ツール許可 |

## 1.5 リスク評価

### Critical リスク

| リスク | 影響 | シナリオ |
|--------|------|---------|
| ホストシステム破壊 | サービス全体停止 | `rm -rf /` がコンテナ内ルートを破壊 |
| 他テナントデータアクセス | 情報漏洩 | `cat /var/lib/aiagent/workspaces/workspace_{他の会話ID}/*` |
| 認証情報窃取 | AWS全リソース侵害 | `env` でAWSクレデンシャル取得 → 外部送信 |
| リソース枯渇 | DoS | Fork bomb, メモリ無限確保 |
| 悪意あるパッケージ | 任意コード実行 | `pip install malicious-package` |
| データ窃取 | 情報漏洩 | `curl` でDBの接続文字列を外部に送信 |

### High リスク

| リスク | 影響 | シナリオ |
|--------|------|---------|
| Python グローバル汚染 | 他セッション影響 | `pip install` がグローバル環境を変更 |
| ファイルシステム汚染 | ディスク枯渇 | 大量ファイル生成でボリューム枯渇 |
| ネットワーク悪用 | 外部攻撃の踏み台 | エージェント経由でのポートスキャン |
| MCP サーバー乗っ取り | 権限昇格 | MCP プロセスの PID 特定 → シグナル送信 |
