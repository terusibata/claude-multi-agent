# Phase 4: システム動作安定化 大規模移行計画書

## 概要

Phase 1〜3 を経てコンテナ隔離アーキテクチャの骨格は実装されたが、
コード精査の結果、**SDK API不整合**、**コンテナ内通信経路の根本的問題**、
**リソース管理の不備**など、動作を妨げる深刻な問題が多数残存していることが判明した。

Phase 4 では、これらの問題を体系的に修正し、
**docker-compose up → コンテナ作成 → エージェント実行 → レスポンス返却** の
一連のフローが端から端まで動作する状態を達成する。

### Phase 4 の方針

- **後方互換性は重視しない**: 開発中のため、レガシー対応コード・フォールバックは排除し、ベストプラクティスに統一する
- **claude-agent-sdk の最新安定版に追従**: 0.1.23 → 最新版に更新し、公式APIに準拠する
- **デフォルト値をセキュアに**: userns-remap有効、seccompプロファイル適用をデフォルトとする
- **未使用コード・デッドコードの積極的除去**

---

## 発見された問題一覧

### 重大度: CRITICAL（システム動作不可）

| ID | ファイル | 概要 |
|----|---------|------|
| P4-01 | `workspace_agent/sdk_client.py` | `_build_sdk_options()` が plain dict を返しているが、SDK は `ClaudeAgentOptions` dataclass を要求。`budget_tokens` / `max_iterations` は存在しないパラメータ |
| P4-02 | `workspace_agent/sdk_client.py` | `_message_to_dict()` がSDKの `Message` union型（`UserMessage`, `AssistantMessage`, `ResultMessage` 等）を正しくSSEイベントに変換していない |
| P4-03 | `workspace_agent/sdk_client.py` | `permission_mode` 未設定。SDKがCLIサブプロセス経由で対話的な権限確認を試みるため、自動実行が停止する |
| P4-04 | `workspace-base/Dockerfile`<br>`workspace-requirements.txt` | `claude-agent-sdk==0.1.23` はPyPI上の古いバージョン。最新は0.1.33。APIの安定性・バグ修正の観点から更新が必要 |
| P4-05 | コンテナアーキテクチャ全体 | SDK は Claude Code CLI（Node.js）をサブプロセスとして起動する。`ReadonlyRootfs` + `noexec tmpfs` 環境で CLI が一時ファイル書き込み・設定ディレクトリ作成できるか未検証 |
| P4-06 | コンテナ環境変数 | `ANTHROPIC_BASE_URL=http+unix:///var/run/ws/proxy.sock` がSDK/CLI で認識されるか未検証。CLIが直接APIアクセスを試みる場合、`--network none` で通信失敗 |

### 重大度: HIGH（主要機能の不全）

| ID | ファイル | 概要 |
|----|---------|------|
| P4-07 | コンテナ環境変数 | `HTTP_PROXY=http+unix://...` は pip, npm, curl で動作しない。`http+unix://` スキームは標準的なプロキシURLとして認識されないため、コンテナ内パッケージインストール不可 |
| P4-08 | `app/services/container/orchestrator.py` | `destroy_all()` が `self._proxies` 内のProxyプロセスを停止せずにコンテナを直接破棄。Proxyリーク |
| P4-09 | `app/services/execute_service.py` | `_sync_files_to_container` で `orchestrator.get_or_create()` を二重呼び出し。既に取得済みの `ContainerInfo` を使い回すべき |
| P4-10 | `app/services/container/gc.py` | Redisメタデータが失われた実行中コンテナ（orphan）を回収しない。Running状態のチェックが回収を妨げる |
| P4-11 | ソケットディレクトリ | `userns_remap_enabled=False` 時のソケットディレクトリパーミッションが未考慮。appuser (UID 1000) がディレクトリ書き込み不可になる可能性 |
| P4-12 | `workspace_agent/sdk_client.py` | SDKのenv パラメータ (`ClaudeAgentOptions.env`) でProxy設定を渡す実装がない。コンテナ環境変数に頼っているが、CLI起動時に明示的に渡す必要がある可能性 |
| P4-13 | `app/services/proxy/dns_cache.py` | Phase 2 で作成した DNSCache が `credential_proxy.py` に統合されていない |

### 重大度: MEDIUM（機能改善・ベストプラクティス移行）

| ID | ファイル | 概要 |
|----|---------|------|
| P4-14 | `app/config.py` | `userns_remap_enabled` デフォルト `False` → 開発環境では `True` をデフォルトにすべき（後方互換性不要方針） |
| P4-15 | `app/config.py` | `seccomp_profile_path` デフォルト空文字 → `deployment/seccomp/workspace-seccomp.json` をデフォルト適用 |
| P4-16 | `app/services/container/warm_pool.py` | `asyncio.create_task()` で生成したタスクの参照を保持していない。例外が飲み込まれる。`TaskGroup` への移行推奨 |
| P4-17 | `app/services/proxy/credential_proxy.py` | `_handle_connect` と `_handle_connection` の両方で `writer.close()` が呼ばれる（二重クローズ）。制御フロー整理が必要 |
| P4-18 | `app/services/container/gc.py` | `proxy_stop_callback` の型アノテーション `"asyncio.coroutines | None"` が不正。`Callable[[str], Awaitable[None]] | None` に修正 |
| P4-19 | `app/services/conversation_service.py` L282 | `delete_conversation` 内で `S3StorageBackend()` を直接インスタンス化。S3未設定時にboto3初期化エラー |
| P4-20 | テスト全般 | 新規コンポーネント（orchestrator, proxy, gc, file_sync, sdk_client）のテストカバレッジが不十分 |

---

## 実装計画

### ステップ 1: Claude Agent SDK の公式API準拠 & バージョン更新

**目標**: workspace_agent がSDK公式APIに完全準拠し、正しくエージェント実行できる状態にする

**対象**: P4-01, P4-02, P4-03, P4-04

**タスク**:

1-1. `workspace-requirements.txt` のSDKバージョンを最新安定版に更新
  - `claude-agent-sdk==0.1.23` → `claude-agent-sdk>=0.1.33` に変更
  - 合わせて Node.js の `@anthropic-ai/claude-code` npm パッケージはSDKにバンドルされるため、workspace-base/Dockerfile の `npm install -g @anthropic-ai/claude-code` 行を削除

1-2. `workspace_agent/sdk_client.py` の `_build_sdk_options()` を `ClaudeAgentOptions` に書き換え
  - `from claude_agent_sdk import ClaudeAgentOptions` を使用
  - `budget_tokens` → `max_budget_usd` に変更（または削除）
  - `max_iterations` → `max_turns` に変更
  - `mcp_servers` は `dict[str, McpServerConfig]` 形式で渡す
  - `permission_mode='bypassPermissions'` を設定（コンテナ内自動実行のため）
  - `env` パラメータで `ANTHROPIC_BASE_URL` を明示的に渡す
  - `cwd="/workspace"` を設定

1-3. `workspace_agent/sdk_client.py` の `_message_to_dict()` をSDKのMessage型に対応
  - `AssistantMessage` → `text_delta` / `tool_use` SSEイベントに変換
    - `TextBlock` → `{"event": "text_delta", "data": {"text": block.text}}`
    - `ToolUseBlock` → `{"event": "tool_use", "data": {"tool_use_id": block.id, "tool_name": block.name}}`
    - `ToolResultBlock` → `{"event": "tool_result", "data": {...}}`
    - `ThinkingBlock` → `{"event": "thinking", "data": {"content": block.thinking}}`
  - `ResultMessage` → `result` SSEイベントに変換
    - `duration_ms`, `num_turns`, `total_cost_usd`, `usage`, `result`, `is_error` を抽出
  - `SystemMessage` → `system` SSEイベント（情報付加用）
  - 不明なメッセージ型は安全にスキップ（ログ出力のみ）

1-4. `workspace_agent/sdk_client.py` の `execute_streaming()` を公式APIパターンで書き直し
  - `query()` 関数の正しい呼び出しパターン:
    ```python
    async for message in query(prompt=user_input, options=options):
        yield _message_to_sse(message)
    ```

1-5. `workspace_agent/models.py` の `ExecuteRequest` スキーマ確認・更新
  - `mcp_servers` の型を `dict[str, Any]` に変更（SDK の McpServerConfig 形式に合わせる）
  - `system_prompt` が str であることを確認

**検証**:
- `python -c "from claude_agent_sdk import query, ClaudeAgentOptions; print('OK')"` が成功
- `workspace_agent/sdk_client.py` のimportエラーがないことを確認
- ユニットテスト: mock SDK を使った `execute_streaming()` の出力形式テスト

---

### ステップ 2: コンテナ環境・Proxy通信経路の確立

**目標**: `--network none` コンテナ内からSDK/CLIが外部APIに到達できる通信経路を確立する

**対象**: P4-05, P4-06, P4-07, P4-12

**タスク**:

2-1. SDK CLI のコンテナ内動作検証と環境調整
  - Claude Agent SDK は内部で Claude Code CLI（Node.jsプロセス）をサブプロセスとして起動する
  - `ReadonlyRootfs=True` 環境でCLIが動作するか検証:
    - CLIの設定ディレクトリ `~/.claude/` → `/home/appuser/` は tmpfs (rw) なので書き込み可能
    - CLIの一時ファイル → `/tmp/` は tmpfs (rw, noexec) だが、Node.jsスクリプト実行には影響しない
  - `PidsLimit=100` がCLI + Node.jsプロセス群で足りるか検証。不足なら 256 に引き上げ
  - CLIバイナリは `/opt/venv/bin/` (read-only rootfs上) に配置されるので実行可能

2-2. コンテナ内 TCP→UDS リバースプロキシの導入
  - 問題: `HTTP_PROXY=http+unix://...` は pip/npm/curl/SDK CLI で動作しない
  - 解決策: コンテナ内に軽量な TCP→UDS プロキシを配置
    - `socat` をベースイメージに追加 (`apt-get install socat`)
    - コンテナ起動スクリプトで `socat TCP-LISTEN:8080,fork,bind=127.0.0.1 UNIX-CONNECT:/var/run/ws/proxy.sock &` を起動
    - 環境変数を変更: `HTTP_PROXY=http://127.0.0.1:8080`, `HTTPS_PROXY=http://127.0.0.1:8080`
    - `ANTHROPIC_BASE_URL` はSDKの `env` パラメータで `http://127.0.0.1:8080` を渡す
  - `--network none` でも loopback (127.0.0.1) は使用可能
  - workspace-base/Dockerfile と workspace_agent/main.py のCMDを更新

2-3. 環境変数の整理
  - コンテナ作成設定 (`app/services/container/config.py`) の `Env` を更新:
    ```
    ANTHROPIC_BASE_URL=http://127.0.0.1:8080
    HTTP_PROXY=http://127.0.0.1:8080
    HTTPS_PROXY=http://127.0.0.1:8080
    NO_PROXY=localhost,127.0.0.1
    ```
  - `http+unix://` 参照を全て除去

2-4. workspace_agent/sdk_client.py で env パラメータ経由のProxy設定
  - `ClaudeAgentOptions.env` にプロキシ設定を明示的に渡す:
    ```python
    env={
        "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8080"),
        "HTTP_PROXY": os.environ.get("HTTP_PROXY", "http://127.0.0.1:8080"),
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:8080"),
    }
    ```

2-5. workspace-base/Dockerfile の更新
  - `socat` パッケージの追加
  - npm の `@anthropic-ai/claude-code` グローバルインストール行を削除（SDKにバンドル済み）
  - CMD をエントリポイントスクリプトに変更し、socat起動 → agent起動の順で実行

**検証**:
- workspace-base コンテナ内で `curl -x http://127.0.0.1:8080 https://pypi.org` が疎通確認
- コンテナ内で `pip install --proxy http://127.0.0.1:8080 requests` が成功
- SDK の `query()` が Proxy 経由で API に到達できること（モックAPIで検証）

---

### ステップ 3: Orchestrator・Proxy リソース管理の修正

**目標**: コンテナとProxyのライフサイクルが正しく管理され、リソースリークがない状態にする

**対象**: P4-08, P4-09, P4-10, P4-11, P4-13

**タスク**:

3-1. `orchestrator.py` の `destroy_all()` にProxy停止処理を追加 (P4-08)
  - `self._proxies` を走査し、全Proxyを停止してから各コンテナを破棄
  - `_cleanup_container()` と同じクリーンアップシーケンスを適用

3-2. コンテナ破棄ロジックの一元化 (P4-08関連)
  - Orchestrator と GC の両方にコンテナ破棄ロジックが存在する現状を解消
  - `_cleanup_container()` を唯一の破棄パスとし、GC からも Orchestrator 経由で破棄を実行
  - クリーンアップシーケンス: Proxy停止 → ファイル同期 → コンテナ停止/削除 → Redis削除 → メトリクス更新

3-3. `execute_service.py` の `_sync_files_to_container` 修正 (P4-09)
  - `orchestrator.get_or_create()` の二重呼び出しを排除
  - `_stream_from_container()` で取得した `ContainerInfo` を同期処理に渡す

3-4. GC の孤立コンテナ回収ロジック修正 (P4-10)
  - Redisにメタデータがなく Docker上で Running 状態のコンテナも回収対象にする
  - ラベル `workspace=true` がついた Running コンテナで、Redis に対応エントリがない場合は強制破棄
  - 安全措置: 作成から5分以上経過したコンテナのみ対象（作成直後の正常コンテナを誤回収しないため）

3-5. ソケットディレクトリのパーミッション修正 (P4-11)
  - `lifecycle.py` の `_setup_socket_directory()` で、userns_remap の有効/無効に関わらず `os.chmod(socket_dir, 0o777)` を設定
  - 現在は userns_remap 有効時のみ 0o777 にしている

3-6. DNSCache の credential_proxy.py への統合 (P4-13)
  - `CredentialInjectionProxy.__init__()` で `DNSCache` をインスタンス化
  - `_forward_request()` 内でホスト名解決に `DNSCache.resolve()` を使用
  - キャッシュTTLはデフォルト300秒

**検証**:
- `destroy_all()` 後に `ps aux | grep socat` / `ss -l` でProxyプロセスが残っていないこと
- GC 実行後に孤立コンテナが回収されること
- ソケットディレクトリのパーミッションが 777 であること
- DNS解決のキャッシュヒットがログに表示されること

---

### ステップ 4: 設定のベストプラクティス移行

**目標**: 後方互換性コードを排除し、セキュリティベストプラクティスをデフォルト適用する

**対象**: P4-14, P4-15

**タスク**:

4-1. `app/config.py` の `userns_remap_enabled` デフォルトを `True` に変更 (P4-14)
  - 開発環境でもuserns-remapを前提とする
  - docker-compose.yml に userns-remap の前提条件をコメントで明記
  - `deployment/docker/daemon.json` を開発環境にも適用する手順をドキュメント化

4-2. `app/config.py` の `seccomp_profile_path` デフォルトを設定 (P4-15)
  - `seccomp_profile_path: str = "deployment/seccomp/workspace-seccomp.json"` に変更
  - 相対パスの場合はアプリケーションルートからの解決ロジックを追加
  - `container/config.py` の `get_container_create_config()` で絶対パスに変換

4-3. コンテナ作成設定の `UsernsMode` 条件分岐を削除
  - 常に userns-remap がデーモンレベルで有効な前提とし、`UsernsMode` をコンテナ設定から除去
  - ※ userns-remap はデーモン設定のため、コンテナ単位での指定は本来不要

4-4. `.env.example` の更新
  - 新しいデフォルト値を反映
  - 不要になった後方互換性設定項目を削除

**検証**:
- `get_settings()` のデフォルト値が期待通りであること
- seccomp プロファイルパスが正しく解決されること
- コンテナ作成設定に `seccomp=...` が含まれること

---

### ステップ 5: コード品質改善・デッドコード除去

**目標**: 型安全性の向上、未使用コードの除去、制御フローの整理

**対象**: P4-16, P4-17, P4-18, P4-19

**タスク**:

5-1. `warm_pool.py` の fire-and-forget タスク修正 (P4-16)
  - `asyncio.create_task()` の返り値を `set` で保持し、完了時に除去するパターンを実装
    ```python
    self._background_tasks: set[asyncio.Task] = set()
    task = asyncio.create_task(self._replenish())
    self._background_tasks.add(task)
    task.add_done_callback(self._background_tasks.discard)
    ```
  - タスクの例外をログ出力する `_on_task_done` コールバックを追加

5-2. `credential_proxy.py` の writer 二重クローズ修正 (P4-17)
  - `_handle_connect()` から writer クローズ処理を除去
  - writer のクローズは `_handle_connection()` の finally ブロックに一元化
  - `_handle_connect()` は tunneling 完了後に正常 return し、caller がクリーンアップ

5-3. `gc.py` の型アノテーション修正 (P4-18)
  - `proxy_stop_callback: "asyncio.coroutines | None"` → `Callable[[str], Awaitable[None]] | None`
  - `from collections.abc import Callable, Awaitable` を追加

5-4. `conversation_service.py` の S3StorageBackend 直接インスタンス化修正 (P4-19)
  - `delete_conversation` 内の `S3StorageBackend()` 呼び出しにS3設定チェックを追加
  - `settings.s3_bucket_name` が空の場合はS3削除処理をスキップ

5-5. レガシーコードの除去
  - `execute_service.py` の旧インプロセス実行パス（`_stream_in_process`）が残っている場合は削除
  - コンテナ隔離が唯一の実行パスとする（後方互換性不要方針）
  - 不要な `hasattr` チェック・フォールバック分岐を除去

**検証**:
- `mypy app/` で型エラーが発生しないこと
- バックグラウンドタスクの例外がログに記録されること
- S3未設定時の会話削除がエラーなしで完了すること

---

### ステップ 6: workspace-base Dockerイメージの再構築

**目標**: コンテナイメージが全ての変更を反映し、正しくビルド・起動できる

**対象**: ステップ 1〜5 の変更を workspace-base に反映

**タスク**:

6-1. `workspace-base/Dockerfile` の更新
  - `socat` パッケージの追加
  - `npm install -g @anthropic-ai/claude-code` 行の削除（SDKにバンドル）
  - エントリポイントスクリプトの作成:
    ```
    1. socat TCP→UDS プロキシ起動 (background)
    2. python -m workspace_agent.main 起動
    ```
  - `PidsLimit` 対応のため、不要なプロセス起動を最小化

6-2. `workspace-requirements.txt` の更新
  - `claude-agent-sdk>=0.1.33` に更新

6-3. `workspace_agent/main.py` の更新
  - agent.sock パスの確認（`/var/run/ws/agent.sock`）
  - ヘルスチェックエンドポイントの内容充実（SDK CLIプロセスの生存確認等）

6-4. HEALTHCHECK の確認
  - `curl --unix-socket /var/run/ws/agent.sock http://localhost/health` が正常動作すること

**検証**:
- `docker build -t workspace-base:latest -f workspace-base/Dockerfile .` が成功
- コンテナ内で `python -c "from claude_agent_sdk import query; print('OK')"` が成功
- コンテナ内で `socat` プロセスが起動していること
- HEALTHCHECK が成功すること

---

### ステップ 7: execute_service のフロー最適化

**目標**: エージェント実行のエンドツーエンドフローが正しく動作する

**タスク**:

7-1. `execute_service.py` の `_stream_from_container()` フロー確認
  - コンテナ取得 → ファイル同期 → agent.sock 経由 POST /execute → SSE中継 → ファイル同期
  - 各ステップのエラーハンドリングが適切であること

7-2. SSEイベントの変換チェーン確認
  - workspace_agent が返す SSE → execute_service が中継 → API がクライアントに返す
  - 各段階でのイベント形式の整合性を確認

7-3. agent.sock への HTTP リクエスト実装確認
  - `orchestrator.execute()` が `httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=...))` を使用
  - UDS 経由の POST /execute リクエストのタイムアウト設定確認（10分/リクエスト）

7-4. ResultMessage からのコスト・トークン情報抽出
  - SDK の `ResultMessage` が返す `total_cost_usd`, `usage`, `num_turns`, `duration_ms` を
    execute_service のレスポンスに正しくマッピング
  - `conversation_service.update_conversation_context_status()` に渡すトークン情報の形式確認

7-5. エラー時のコンテナ状態復旧
  - 実行中にコンテナがクラッシュした場合の `container_lost` エラー処理
  - Redis 状態の整合性維持

**検証**:
- モック SDK を使った execute_service の E2E テスト
- SSE イベントの形式が API 仕様に準拠していること
- エラー時にリソースリークがないこと

---

### ステップ 8: 統合テスト・E2E動作検証

**目標**: 全修正が正しく機能することをシステムレベルで確認

**タスク**:

8-1. docker-compose 起動テスト
  - `docker-compose up` でバックエンド、PostgreSQL、Redis が起動すること
  - `/health`, `/health/live`, `/health/ready` が全て 200 を返すこと
  - WarmPool プリヒートが完了し、warm コンテナが待機すること

8-2. workspace-base イメージのビルド・起動テスト
  - イメージビルドが成功すること
  - コンテナ内で socat + workspace_agent が起動すること
  - HEALTHCHECK が成功すること

8-3. コンテナ隔離フロー E2E テスト
  - `orchestrator.get_or_create()` でコンテナ取得
  - Proxy 起動 → socat 経由の通信確立
  - agent.sock 経由で /execute POST
  - SSE レスポンスがクライアントに返却されること
  - 実行完了後のファイル同期確認

8-4. セキュリティ制約テスト
  - `--network none` が有効であること（コンテナ内から外部IPへの直接通信が不可）
  - `ReadonlyRootfs` で / への書き込みが拒否されること
  - `PidsLimit` でfork bombが制限されること
  - seccomp プロファイルが適用されていること
  - userns-remap が有効であること

8-5. GC・ライフサイクルテスト
  - 非アクティブTTL経過後にコンテナが回収されること
  - 回収時にProxy停止、メトリクス更新が行われること
  - 孤立コンテナの回収テスト

8-6. 既存テストの更新
  - `tests/integration/test_phase2.py` を Phase 4 変更に合わせて更新
  - 新規回帰テストの追加
  - SDKモック を使った workspace_agent のユニットテスト追加 (P4-20)

**検証**:
- 全テストがパスすること
- ログにエラーが記録されていないこと
- メトリクスが正確であること

---

## 実装順序と依存関係

```
ステップ 1 (SDK API準拠)          ステップ 4 (設定ベストプラクティス)
    │                                │
    ▼                                ▼
ステップ 2 (通信経路確立)      ステップ 5 (コード品質改善)
    │                                │
    ├────────────────┬───────────────┘
    ▼                ▼
ステップ 3 (リソース管理修正)
    │
    ▼
ステップ 6 (Dockerイメージ再構築)
    │
    ▼
ステップ 7 (execute_service最適化)
    │
    ▼
ステップ 8 (統合テスト・E2E検証)
```

**並行可能**: ステップ 1 と 4, ステップ 2 と 5
**クリティカルパス**: ステップ 1 → 2 → 3 → 6 → 7 → 8

---

## 修正対象ファイル一覧

| ファイル | ステップ | 修正内容 |
|---------|---------|---------|
| `workspace_agent/sdk_client.py` | 1 | SDK公式API準拠（ClaudeAgentOptions, Message型変換, permission_mode） |
| `workspace_agent/models.py` | 1 | ExecuteRequest スキーマ更新 |
| `workspace-requirements.txt` | 1, 6 | SDK バージョン更新 |
| `workspace-base/Dockerfile` | 2, 6 | socat追加、npm install削除、エントリポイントスクリプト |
| `app/services/container/config.py` | 2, 4 | 環境変数・seccomp・userns設定更新 |
| `workspace_agent/main.py` | 2, 6 | 起動スクリプト・ヘルスチェック更新 |
| `app/services/container/orchestrator.py` | 3 | destroy_all() Proxy停止、破棄ロジック一元化 |
| `app/services/execute_service.py` | 3, 7 | 二重get_or_create排除、SSE変換チェーン、フロー最適化 |
| `app/services/container/gc.py` | 3 | 孤立コンテナ回収、型アノテーション修正 |
| `app/services/container/lifecycle.py` | 3 | ソケットパーミッション修正 |
| `app/services/proxy/credential_proxy.py` | 3, 5 | DNSCache統合、writer二重クローズ修正 |
| `app/config.py` | 4 | userns/seccompデフォルト値変更 |
| `.env.example` | 4 | デフォルト値更新 |
| `app/services/container/warm_pool.py` | 5 | タスク参照保持パターン |
| `app/services/conversation_service.py` | 5 | S3未設定時のガード追加 |
| `tests/integration/test_phase2.py` | 8 | テスト更新 |
| `tests/` (新規) | 8 | workspace_agent, orchestrator, proxy のユニットテスト |

---

## リスクと注意事項

1. **ステップ 1（SDK API準拠）が最重要**: SDK のAPIが想定通りでない場合、workspace_agent の大幅な書き直しが必要。作業開始前に最新SDKをインストールし、`query()` の動作を手元で確認すること。

2. **ステップ 2（TCP→UDS プロキシ）はアーキテクチャ変更**: socat の追加はプロセス数を増やし、PidsLimit に影響する。socat 自体のセキュリティ影響も確認が必要。

3. **userns-remap の有効化 (ステップ 4)**: Docker デーモンの再起動が必要。開発環境で他のコンテナに影響が出る可能性がある。daemon.json の変更は慎重に適用すること。

4. **SDK CLI の ReadonlyRootfs 対応 (ステップ 2)**: CLIが予期しないパスに書き込みを試みる可能性がある。その場合はtmpfsの追加マウントで対応する。書き込み先は実際の動作ログで確認。

5. **テストカバレッジ (ステップ 8)**: E2Eテストは Docker デーモンが利用可能な環境でのみ実行可能。CI/CD環境ではDocker-in-Docker またはテスト用Dockerデーモンの準備が必要。

---

## 完了基準

- [ ] `workspace-base:latest` イメージがビルド・起動に成功する
- [ ] コンテナ内で Claude Agent SDK の `query()` がProxy経由でAPI到達できる
- [ ] `docker-compose up` でバックエンドが起動エラーなしで立ち上がる
- [ ] `/health` エンドポイントが全コンポーネント healthy で200を返す
- [ ] WarmPool プリヒートが完了し、コンテナが待機状態になる
- [ ] agent.sock 経由でワークスペースコンテナと通信が成立する
- [ ] Proxy 経由で HTTP/HTTPS 通信が成功する（pip install, API呼び出し）
- [ ] エージェント実行 → SSEレスポンス返却の E2E フローが動作する
- [ ] GC がコンテナ・Proxy を正しく回収しリソースをクリーンアップする
- [ ] userns-remap 有効・seccomp 適用がデフォルト状態で動作する
- [ ] S3 未設定環境でもエージェント実行がエラーなしで動作する
- [ ] 既存テスト + 新規テストが全てパスする
- [ ] メトリクス（active_containers, warm_pool_size等）が正確に更新される
