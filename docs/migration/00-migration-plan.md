# Workspace Container Isolation - 大規模移行計画書

**作成日**: 2026-02-07
**ステータス**: 計画策定中
**対象ブランチ**: `claude/backend-security-migration-Szg3M`

---

## 1. 現行アーキテクチャの問題分析

### 1.1 Critical（即時対応必須）

| ID | 問題 | 影響範囲 | 現行コード |
|----|------|---------|-----------|
| C-1 | 全セッションが単一FastAPIプロセスで実行 | プロセス/FS/NW共有、テナント間分離なし | `app/services/execute_service.py` でClaude Agent SDKをin-process実行 |
| C-2 | `rm -rf /` やfork bombが全体に影響 | 全テナント停止 | リソース制限なし、コンテナ内に全セッション |
| C-3 | テナント間ファイルアクセス可能 | データ漏洩 | `/var/lib/aiagent/workspaces` を全セッション共有 |

### 1.2 High

| ID | 問題 | 影響範囲 | 現行コード |
|----|------|---------|-----------|
| H-1 | `pip install` が全セッションを汚染 | 環境破壊 | 共有Python環境 |
| H-2 | リソース制限なし | CPU/メモリ枯渇 | docker-compose.yml にプロセスレベル制限なし |
| H-3 | ネットワーク制限なし | 任意の外部通信可能 | コンテナがフルネットワークアクセス |

### 1.3 Medium

| ID | 問題 | 影響範囲 | 現行コード |
|----|------|---------|-----------|
| M-1 | AWS認証情報が環境変数で全セッション共有 | 認証情報漏洩 | `app/main.py:102-108` で `os.environ` に直接設定 |
| M-2 | メタデータサービスへのアクセス可能 | IAMロール奪取 | `--network none` 未使用 |

---

## 2. 移行後アーキテクチャ概要

```
Frontend ─── HTTPS ──→ Backend (FastAPI)
                         ├─ ContainerOrchestrator (aiodocker)
                         │   ├─ get_or_create(conversation_id)
                         │   ├─ destroy(conversation_id)
                         │   └─ execute(conversation_id, request)
                         ├─ WarmPoolManager (Redis)
                         │   ├─ acquire() → ContainerInfo
                         │   └─ replenish()
                         ├─ CredentialInjectionProxy (per container)
                         │   ├─ Unix Socket listen
                         │   ├─ ドメインホワイトリスト
                         │   ├─ SigV4注入
                         │   └─ 監査ログ
                         └─ WorkspaceFileSync
                             ├─ sync_to_container (S3 → Container)
                             └─ sync_from_container (Container → S3)
                                    │
                                    │ Unix Socket
                                    ▼
                            Container (--network none)
                              ├─ workspace_agent (FastAPI over UDS)
                              │   ├─ ClaudeSDKClient (in-process)
                              │   ├─ Claude Code CLI (Node.js)
                              │   └─ Builtin MCP Servers
                              ├─ /opt/venv (プリインストール済み)
                              └─ /workspace (S3同期)
```

---

## 3. ギャップ分析 - 変更対象ファイル

### 3.1 新規作成ファイル

| ファイル | 目的 |
|---------|------|
| `workspace-base/Dockerfile` | コンテナベースイメージ |
| `workspace-base/workspace-requirements.txt` | コンテナ内Python依存パッケージ |
| `workspace_agent/__init__.py` | コンテナ内エージェントパッケージ |
| `workspace_agent/main.py` | コンテナ内FastAPI (UDS) |
| `workspace_agent/sdk_client.py` | Claude SDK クライアントラッパー |
| `workspace_agent/models.py` | リクエスト/レスポンススキーマ |
| `app/services/container/__init__.py` | コンテナオーケストレーションパッケージ |
| `app/services/container/orchestrator.py` | ContainerOrchestrator |
| `app/services/container/warm_pool.py` | WarmPoolManager |
| `app/services/container/gc.py` | ガベージコレクター |
| `app/services/container/lifecycle.py` | コンテナライフサイクル管理 |
| `app/services/container/config.py` | コンテナ設定定義 |
| `app/services/container/models.py` | ContainerInfo等のデータモデル |
| `app/services/proxy/__init__.py` | プロキシパッケージ |
| `app/services/proxy/credential_proxy.py` | CredentialInjectionProxy |
| `app/services/proxy/domain_whitelist.py` | ドメインホワイトリスト |
| `app/services/proxy/sigv4.py` | AWS SigV4署名 |
| `app/services/workspace/file_sync.py` | Container ↔ S3ファイル同期 |

### 3.2 大幅改修ファイル

| ファイル | 変更内容 |
|---------|---------|
| `app/services/execute_service.py` | in-process SDK実行 → コンテナ経由実行に全面書き換え |
| `app/main.py` | ContainerOrchestrator/GC初期化、シャットダウン時コンテナ破棄 |
| `app/config.py` | コンテナ/プロキシ/WarmPool設定の追加 |
| `docker-compose.yml` | Docker Socket マウント、ネットワーク設定追加 |
| `Dockerfile` | aiodocker等の依存追加、Docker Socket対応 |
| `requirements.txt` | aiodocker, botocore(SigV4) 等の追加 |

### 3.3 中程度改修ファイル

| ファイル | 変更内容 |
|---------|---------|
| `app/api/conversations.py` | ストリーミングをコンテナSSEプロキシに変更 |
| `app/services/workspace_service.py` | コンテナ対応ファイル同期 |
| `app/services/workspace/s3_storage.py` | コンテナ同期メソッド追加 |
| `app/api/health.py` | コンテナヘルスチェック追加 |
| `app/infrastructure/redis.py` | WarmPool/コンテナ管理用キーパターン |

### 3.4 変更不要ファイル（API互換性維持）

| ファイル群 | 理由 |
|-----------|------|
| `app/models/*.py` | DBスキーマ変更不要（Phase 1） |
| `app/schemas/*.py` | API リクエスト/レスポンス形式は維持 |
| `app/middleware/*.py` | 認証/レート制限/トレーシングはそのまま |
| `app/services/tenant_service.py` | テナント管理はそのまま |
| `app/services/model_service.py` | モデル管理はそのまま |
| `app/services/skill_service.py` | スキル管理はそのまま |
| `app/services/mcp_server_service.py` | MCPサーバー管理はそのまま |
| `app/services/simple_chat_service.py` | SimpleChat（非SDK）はそのまま |
| `app/services/bedrock_client.py` | SimpleChat用Bedrockクライアント |
| `alembic/` | マイグレーション追加は別途検討 |

---

## 4. 移行タスク一覧（Phase 1: コンテナ隔離 + セキュリティ基盤）

### Step 1: 基盤準備

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 1.1 | 依存パッケージ追加（aiodocker等） | なし | `requirements.txt` |
| 1.2 | コンテナ関連設定の追加 | なし | `app/config.py` |
| 1.3 | ContainerInfo等のデータモデル定義 | なし | `app/services/container/models.py` |
| 1.4 | コンテナ作成設定の定義 | 1.2 | `app/services/container/config.py` |

### Step 2: ワークスペースベースイメージ

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 2.1 | ベースイメージDockerfile作成 | なし | `workspace-base/Dockerfile` |
| 2.2 | コンテナ内Python依存パッケージ定義 | なし | `workspace-base/workspace-requirements.txt` |

### Step 3: コンテナ内エージェント（workspace_agent）

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 3.1 | リクエスト/レスポンスモデル定義 | なし | `workspace_agent/models.py` |
| 3.2 | Claude SDKクライアントラッパー | 3.1 | `workspace_agent/sdk_client.py` |
| 3.3 | FastAPI メインアプリ（UDS対応） | 3.1, 3.2 | `workspace_agent/main.py` |

### Step 4: Credential Injection Proxy

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 4.1 | ドメインホワイトリスト | なし | `app/services/proxy/domain_whitelist.py` |
| 4.2 | AWS SigV4署名ユーティリティ | なし | `app/services/proxy/sigv4.py` |
| 4.3 | CredentialInjectionProxy本体 | 4.1, 4.2 | `app/services/proxy/credential_proxy.py` |

### Step 5: Container Orchestrator

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 5.1 | コンテナライフサイクル管理 | 1.3, 1.4 | `app/services/container/lifecycle.py` |
| 5.2 | ContainerOrchestrator本体 | 5.1, 4.3 | `app/services/container/orchestrator.py` |
| 5.3 | WarmPoolManager | 5.1 | `app/services/container/warm_pool.py` |
| 5.4 | GC（ガベージコレクター） | 5.1 | `app/services/container/gc.py` |

### Step 6: ファイル同期

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 6.1 | Container ↔ S3ファイル同期 | 5.2 | `app/services/workspace/file_sync.py` |
| 6.2 | WorkspaceService改修 | 6.1 | `app/services/workspace_service.py` |

### Step 7: 実行エンジン書き換え

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 7.1 | ExecuteService全面書き換え | 5.2, 6.1 | `app/services/execute_service.py` |
| 7.2 | ストリーミングAPI改修 | 7.1 | `app/api/conversations.py` |

### Step 8: アプリケーション統合

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 8.1 | main.pyにOrchestrator/GC統合 | 5.2, 5.4 | `app/main.py` |
| 8.2 | ヘルスチェックにコンテナ状態追加 | 5.2 | `app/api/health.py` |
| 8.3 | docker-compose.yml更新 | 2.1 | `docker-compose.yml` |
| 8.4 | ホストDockerfile更新 | 1.1 | `Dockerfile` |

### Step 9: セキュリティ検証

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 9.1 | AWS認証情報の環境変数からの除去 | 4.3, 8.1 | `app/main.py` |
| 9.2 | セキュリティレイヤー確認 | 全Step | ドキュメント |

### Step 10: クリーンアップ

| # | タスク | 依存 | 対象ファイル |
|---|--------|------|-------------|
| 10.1 | 不要コード削除 | 7.1 | 旧execute関連 |
| 10.2 | ドキュメント更新 | 全Step | `docs/` |

---

## 5. 実装順序とクリティカルパス

```
Step 1 (基盤準備)
  │
  ├─→ Step 2 (ベースイメージ) ─→ Step 3 (workspace_agent)
  │                                      │
  │                                      ▼
  ├─→ Step 4 (Proxy) ──────────→ Step 5 (Orchestrator)
  │                                      │
  │                                      ├─→ Step 6 (ファイル同期)
  │                                      │        │
  │                                      │        ▼
  │                                      └─→ Step 7 (実行エンジン)
  │                                               │
  │                                               ▼
  └─────────────────────────────────────→ Step 8 (統合)
                                                  │
                                                  ▼
                                          Step 9 (セキュリティ検証)
                                                  │
                                                  ▼
                                          Step 10 (クリーンアップ)
```

**クリティカルパス**: Step 1 → Step 4 → Step 5 → Step 7 → Step 8 → Step 9

---

## 6. リスクと緩和策

| リスク | 影響度 | 緩和策 |
|--------|-------|--------|
| aiodockerのバグ/制限 | 高 | Docker CLIフォールバック実装を検討 |
| Unix Socket通信のSSE互換性 | 高 | httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=...))で検証 |
| コンテナ起動遅延（3-10秒） | 中 | WarmPoolで事前起動、UX面はフロントエンドで対応 |
| Docker Socket権限問題 | 中 | appuserをdockerグループに追加、ソケット権限設定 |
| S3同期のデータ整合性 | 中 | チェックサム検証、リトライ、定期同期 |
| 既存テストの互換性 | 低 | テストはモック対応、統合テストは別途 |

---

## 7. 後方互換性の方針

**後方互換性は重視しない**（仕様書の指示通り）。

- APIエンドポイントのURL/リクエスト形式: **維持**（フロントエンド影響最小化）
- SSEイベント形式: **維持**（フロントエンド影響最小化）
- 内部実装: **全面刷新**（ベストプラクティス優先）
- 旧コード互換シム: **作らない**（不要なものは完全削除）
- 環境変数: **変更あり**（コンテナ関連の新設定追加、旧設定の一部廃止）

---

## 8. Phase 2以降のスコープ（今回対象外）

| Phase | 内容 |
|-------|------|
| Phase 2 | WarmPool最適化、userns-remap、カスタムseccomp、監視ダッシュボード |
| Phase 3 | gVisor (runsc)、AppArmor、セキュリティ監査ログ集約、ペネトレーションテスト |
| Phase 4 | マルチホスト、Auto Scaling、Spotインスタンス、Firecracker microVM |
