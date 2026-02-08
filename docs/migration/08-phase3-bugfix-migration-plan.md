# Phase 3: バグ修正・動作安定化 大規模移行計画書

## 概要

Phase 1（コンテナ隔離 + セキュリティ基盤）および Phase 2（運用品質 + WarmPool）の実装完了後のコードレビューにおいて、
アプリケーション起動を阻害するクリティカルバグ、コンテナ通信を不可能にするアーキテクチャ上の問題、
およびワークスペースイメージの起動失敗を含む多数の不具合が確認された。

Phase 3 では、これらの不具合を体系的に修正し、システムを **最低限動作する状態（MVP）** まで持っていくことを目標とする。

## 発見されたバグ一覧

### 重大度: CRITICAL（アプリ起動不可 / 基幹機能完全停止）

| ID | ファイル | 行 | 概要 |
|----|---------|-----|------|
| BUG-01 | `app/main.py` | 147 | `settings.container_gc_interval` が `config.py` に未定義。起動時 `AttributeError` で即クラッシュ |
| BUG-02 | `app/config.py` | 160 | `docker_socket_path` デフォルト値 `/var/run/docker.sock` に `unix://` プレフィックスが欠落。docker-compose外で `ValueError` |
| BUG-03 | `app/api/health.py` | 77 | `check_redis_health()` が3値タプル返却だが、2変数でアンパック。`/health` エンドポイント常時クラッシュ |
| BUG-06 | `app/services/container/config.py` | 62-63 | ソケットファイルのBind mount先が存在しない状態でコンテナ作成。Dockerがソケットの代わりにディレクトリを作成し、Unix Socket通信が不可能 |
| BUG-07 | `docker-compose.yml` / `config.py` | - | Docker-in-Docker環境でソケットパスの解決不整合。バックエンドコンテナ内パスとホストパスが乖離し、ワークスペースコンテナからソケット到達不能 |
| BUG-08 | `workspace-base/Dockerfile` | 17,26 | `workspace_agent` を `/opt/workspace_agent/` にコピーするが、PYTHONPATH未設定。CMD `python -m workspace_agent.main` で `ModuleNotFoundError` |
| BUG-09 | `workspace-base/Dockerfile` | 23-24 | HEALTHCHECK が `http+unix://` スキームを使用。Python標準ライブラリ `urllib.request` は非対応。ヘルスチェック常時失敗 |

### 重大度: HIGH（機能不全 / データ不整合）

| ID | ファイル | 行 | 概要 |
|----|---------|-----|------|
| BUG-04 | `app/services/proxy/credential_proxy.py` | 134-141 | CONNECT トンネル完了後、`_handle_connection` が既にクローズされた writer に再度HTTPレスポンスを書き込む。HTTPS通信でエラー |
| BUG-05 | `app/config.py` | 161 | `workspace_socket_base_path` デフォルト値 `/var/run/ws` と docker-compose の `/var/run/workspace-sockets` が不一致 |
| BUG-10 | `workspace_agent/sdk_client.py` | 46 | `from claude_agent_sdk import ClaudeSDKClient` のインポート名とクラス名の正確性が未検証。SDK公式APIとの整合性確認が必要 |
| BUG-11 | `app/services/execute_service.py` | 237-262 | ファイル同期で `S3StorageBackend()` を無条件にインスタンス化。S3未設定時（`s3_bucket_name` 空）にAWS API呼び出しで失敗 |
| BUG-12 | `app/services/container/gc.py` | 132-155 | GCがコンテナ破棄時にProxy停止を行わない（Orchestrator経由でない）。Proxyプロセスがリークする |
| BUG-13 | `app/services/container/gc.py` | 132-155 | GCがアクティブコンテナのメトリクス（`workspace_active_containers`）をデクリメントしない。メトリクスが永続的に増加 |

### 重大度: MEDIUM（軽微な問題 / コード品質）

| ID | ファイル | 行 | 概要 |
|----|---------|-----|------|
| BUG-14 | `app/api/conversations.py` | 73 | `get_conversations()` のクエリパラメータ `status` が `fastapi.status` インポートをシャドウイング。関数内でHTTPステータスコード利用不可 |
| BUG-15 | 複数ファイル | - | Redis `decode_responses=True` 設定済みだが、バイトデコード処理 `isinstance(k, bytes)` が残存。デッドコード（機能影響なし） |

## 実装計画

### ステップ 1: アプリケーション起動クリティカルバグ修正

**目標**: FastAPIアプリケーションがエラーなしで起動する状態にする

**対象バグ**: BUG-01, BUG-02, BUG-03, BUG-05

**タスク**:

1-1. `app/config.py` に `container_gc_interval: int = 60` 設定を追加（BUG-01）

1-2. `app/config.py` の `docker_socket_path` デフォルト値を `unix:///var/run/docker.sock` に修正（BUG-02）

1-3. `app/api/health.py` の `check_redis_component_health()` を修正し、`check_redis_health()` の3値タプル返却に対応（BUG-03）

1-4. `app/config.py` の `workspace_socket_base_path` デフォルト値を `/var/run/workspace-sockets` に統一（BUG-05）

**検証**:
- `python -c "from app.config import get_settings; s = get_settings(); print(s.container_gc_interval, s.docker_socket_path, s.workspace_socket_base_path)"` がエラーなしで完了
- `python -c "from app.main import app"` がインポートエラーなしで完了

---

### ステップ 2: ワークスペースベースイメージ修正

**目標**: workspace-base コンテナが起動し、workspace_agent が正常に動作する

**対象バグ**: BUG-08, BUG-09, BUG-10

**タスク**:

2-1. `workspace-base/Dockerfile` で PYTHONPATH を設定し、`/opt/workspace_agent` にコピーされたモジュールが `python -m workspace_agent.main` でインポート可能にする（BUG-08）
  - 方法A: `ENV PYTHONPATH="/opt:$PYTHONPATH"` を追加（`/opt/workspace_agent/` → `import workspace_agent`）
  - 方法B: コピー先を `/opt/venv/lib/python3.11/site-packages/workspace_agent/` に変更
  - 方法C: CMD を `python -m /opt/workspace_agent/main` に変更し、モジュール実行ではなくスクリプト実行にする
  - **推奨**: 方法A（最もシンプル）

2-2. `workspace-base/Dockerfile` の HEALTHCHECK を修正し、`http+unix://` スキーム非対応問題を解決（BUG-09）
  - `urllib.request` は `http+unix://` 非対応のため、代替手段が必要:
  - 方法A: `curl --unix-socket /var/run/agent.sock http://localhost/health` を使用（curl は既にインストール済み）
  - 方法B: 小さなPythonスクリプトで `socket.socket(AF_UNIX)` を直接使用
  - **推奨**: 方法A（最もシンプル、依存なし）

2-3. Claude Agent SDK のインポート名・クラス名を検証（BUG-10）
  - `claude-agent-sdk==0.1.23` パッケージの実際のモジュール名とエクスポートされたクラス名を確認
  - `workspace_agent/sdk_client.py` のインポート文を必要に応じて修正
  - SDKが提供するAPIインターフェース（`ClaudeSDKClient`, `query()`, `receive_response()`）の存在を確認
  - SDKが実際に存在しない場合やAPIが異なる場合は、モック/スタブ実装を検討

**検証**:
- `docker build -t workspace-base:latest -f workspace-base/Dockerfile .` が成功
- コンテナ内で `python -c "from workspace_agent.main import app; print('OK')"` が成功
- HEALTHCHECK コマンドが手動実行で成功

---

### ステップ 3: ソケット通信アーキテクチャ修正

**目標**: ホスト - コンテナ間のUnix Socket通信が正常に確立される

**対象バグ**: BUG-06, BUG-07

**タスク**:

3-1. ソケットBind mountの競合状態を解決（BUG-06）
  - 問題: コンテナ作成時にソケットファイルが存在しないため、Dockerがディレクトリを作成してしまう
  - 解決策: Bind mountをソケットファイル単位ではなく、**ディレクトリ単位**に変更する
    ```
    変更前: {base}/{id}/proxy.sock:/var/run/proxy.sock:ro
    変更後: {base}/{id}:/var/run/ws:rw
    ```
  - コンテナ内のプロセスは `/var/run/ws/agent.sock` にリッスン
  - ホスト側のProxy は `{base}/{id}/proxy.sock` にリッスン
  - 対応ファイル: `app/services/container/config.py`, `workspace_agent/main.py`

3-2. Docker-in-Docker環境でのソケットパス解決を修正（BUG-07）
  - 問題: docker-compose のバックエンドコンテナ内のパスとホスト上のパスが不一致
  - 解決策: Docker Volumeではなく、ホストバインドマウントでソケットディレクトリを共有
    ```yaml
    # docker-compose.yml
    volumes:
      - /var/run/workspace-sockets:/var/run/workspace-sockets
    ```
  - または: 環境変数 `WORKSPACE_SOCKET_HOST_PATH` を追加し、コンテナ作成時のBind mountにはホスト上の実パスを使用
  - 対応ファイル: `docker-compose.yml`, `app/services/container/config.py`, `app/config.py`

3-3. ContainerInfo モデルの `agent_socket` / `proxy_socket` パスをアーキテクチャ変更に合わせて更新
  - `app/services/container/lifecycle.py` のソケットパス生成ロジックを修正
  - `app/services/container/models.py` の必要に応じた変更

3-4. Proxy起動タイミングの見直し
  - 現在: コンテナ作成後にProxy起動（`orchestrator.get_or_create()` 内）
  - ディレクトリBind mount方式では、Proxy起動がコンテナ起動前でも後でも動作するが、
    コンテナ内のagentがProxy到達を待つ仕組み（リトライ or readiness wait）を検討

**検証**:
- バックエンドコンテナ内から、ワークスペースコンテナの agent.sock に接続できる
- ワークスペースコンテナ内から、proxy.sock に接続できる
- `httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=agent_socket))` でリクエストが通る

---

### ステップ 4: Credential Injection Proxy 修正

**目標**: HTTP/HTTPS両方のプロキシ通信が正常に機能する

**対象バグ**: BUG-04

**タスク**:

4-1. `credential_proxy.py` の `_handle_connection()` でCONNECTメソッド処理後の制御フローを修正（BUG-04）
  - CONNECT成功時は `_handle_connect()` 内でレスポンス送信とトンネリングが完了するため、
    `_handle_connection()` に戻った後の追加レスポンス送信をスキップする
  - 方法: `_handle_connect()` の戻り値で分岐するか、CONNECTの場合は early return

4-2. Proxy停止時のリソースリーク確認
  - `_handle_connect()` 内の `_pipe()` で接続先ソケットの確実なクローズを保証
  - `asyncio.gather()` の例外ハンドリングを改善

**検証**:
- HTTP平文リクエスト（pip install、pypi.org）が成功
- HTTPS CONNECTリクエスト（api.anthropic.com）が成功
- ドメインホワイトリスト外のリクエストが403で拒否される
- 10回連続リクエストでエラーログが出ない

---

### ステップ 5: GC（ガベージコレクター）修正

**目標**: GCがコンテナを正しくクリーンアップし、メトリクスが正確に維持される

**対象バグ**: BUG-12, BUG-13

**タスク**:

5-1. GCにProxy停止機能を追加（BUG-12）
  - GCがOrchestrator経由でコンテナ破棄を行う（Orchestratorの `_cleanup_container` を呼ぶ）か、
    GC自身がProxy参照を保持してクリーンアップする
  - 推奨: GCの `_graceful_destroy()` に Orchestrator への参照を追加し、Proxy停止を委譲

5-2. GCにメトリクスデクリメントを追加（BUG-13）
  - `_graceful_destroy()` 完了後に `get_workspace_active_containers().dec()` を呼び出す

5-3. GCとOrchestratorのコンテナ破棄ロジックを統一（リファクタリング）
  - 現在、コンテナ破棄が2箇所（Orchestrator._cleanup_container, GC._graceful_destroy）に分散
  - 共通のクリーンアップ関数を作成し、Proxy停止 + コンテナ破棄 + Redis削除 + メトリクス更新を1箇所に集約

**検証**:
- GC実行後にProxy プロセスが残っていないこと
- GCでコンテナ破棄後、`workspace_active_containers` メトリクスが正確にデクリメントされること

---

### ステップ 6: ファイル同期の安全性確保

**目標**: S3未設定環境でもアプリケーションが正常動作する

**対象バグ**: BUG-11

**タスク**:

6-1. `execute_service.py` の `_sync_files_to_container()` / `_sync_files_from_container()` にS3設定チェックを追加
  - `settings.s3_bucket_name` が空の場合はファイル同期をスキップ（ログ出力のみ）
  - S3StorageBackend のインスタンス化を遅延させ、必要な場合のみ生成

6-2. `WorkspaceFileSync` にS3未設定時のグレースフルフォールバックを実装
  - S3が設定されていない場合は何もせずに0を返す

**検証**:
- `s3_bucket_name` が空の状態でエージェント実行がエラーなしで完了する
- S3設定済み環境では、コンテナ起動時にS3→コンテナへのファイル同期が動作する

---

### ステップ 7: コード品質改善

**目標**: デッドコード除去、シャドウイング解消、コードの一貫性向上

**対象バグ**: BUG-14, BUG-15

**タスク**:

7-1. `conversations.py` の `get_conversations()` パラメータ `status` の変数名をリネーム（BUG-14）
  - `status` → `status_filter` に変更し、`fastapi.status` とのシャドウイングを解消
  - ConversationService の対応するパラメータ名も更新

7-2. Redis バイトデコード処理のデッドコード除去（BUG-15）
  - `decode_responses=True` が設定されているため、`isinstance(k, bytes)` チェックは不要
  - 対象ファイル: `orchestrator.py`, `gc.py`, `warm_pool.py`
  - 直接 `str` として扱うように簡素化

**検証**:
- 全テストが通ること
- Pythonの静的解析（mypy/pyright）で型エラーが出ないこと

---

### ステップ 8: 統合テスト・動作検証

**目標**: 全修正が正しく機能することをend-to-endで確認

**タスク**:

8-1. ローカル環境でのdocker-compose起動テスト
  - `docker-compose up` でバックエンド、DB、Redis が起動すること
  - `/health` エンドポイントが200を返すこと
  - `/health/live` が200を返すこと
  - `/health/ready` が200を返すこと

8-2. workspace-base イメージのビルド・起動テスト
  - `docker build -t workspace-base:latest -f workspace-base/Dockerfile .` が成功
  - コンテナ内で `python -m workspace_agent.main` が起動すること
  - HEALTHCHECK が成功すること

8-3. コンテナ隔離フローのE2Eテスト
  - WarmPool プリヒートでコンテナが作成されること
  - `orchestrator.get_or_create()` でコンテナが取得できること
  - Unix Socket経由で `/execute` にリクエストが到達すること
  - SSEレスポンスがクライアントに中継されること
  - GCが非アクティブコンテナを正しく破棄すること

8-4. Proxy通信テスト
  - HTTP平文リクエストがProxyを通過すること
  - HTTPS CONNECTリクエストがProxyを通過すること
  - ドメインホワイトリスト外が403拒否されること
  - Bedrock APIへのリクエストにSigV4が注入されること

8-5. 既存テストの修正・更新
  - Phase 2 の統合テスト (`tests/integration/test_phase2.py`) が修正後のコードで通ること
  - 新規バグに対する回帰テストを追加

---

## 実装順序と依存関係

```
ステップ 1 (起動バグ修正)
    │
    ├── ステップ 2 (ベースイメージ修正)  ← 並行可能
    │
    └── ステップ 3 (ソケット通信修正)  ← ステップ1完了後
            │
            ├── ステップ 4 (Proxy修正)  ← ステップ3完了後
            │
            └── ステップ 5 (GC修正)  ← ステップ3完了後、並行可能
                    │
                    └── ステップ 6 (ファイル同期)  ← ステップ5完了後
                            │
                            └── ステップ 7 (コード品質)  ← 並行可能
                                    │
                                    └── ステップ 8 (統合テスト)  ← 全ステップ完了後
```

**クリティカルパス**: ステップ 1 → 3 → 4/5 → 6 → 8

## 修正対象ファイル一覧

| ファイル | 修正ステップ | 修正内容 |
|---------|-------------|---------|
| `app/config.py` | 1 | GC間隔設定追加、Dockerソケットパス修正、ソケットベースパス修正 |
| `app/api/health.py` | 1 | Redis ヘルスチェックの3値タプルアンパック修正 |
| `workspace-base/Dockerfile` | 2 | PYTHONPATH設定、HEALTHCHECK修正 |
| `workspace_agent/sdk_client.py` | 2 | SDKインポート名・API整合性修正 |
| `app/services/container/config.py` | 3 | Bind mount方式をディレクトリ単位に変更 |
| `app/services/container/lifecycle.py` | 3 | ソケットパス生成ロジック修正 |
| `workspace_agent/main.py` | 3 | agent.sockパスをアーキテクチャ変更に合わせて更新 |
| `docker-compose.yml` | 3 | ソケットボリューム設定をホストバインドマウントに変更 |
| `app/services/proxy/credential_proxy.py` | 4 | CONNECT後の二重レスポンス修正 |
| `app/services/container/gc.py` | 5 | Proxy停止追加、メトリクスデクリメント追加 |
| `app/services/container/orchestrator.py` | 5 | クリーンアップロジック統一 |
| `app/services/execute_service.py` | 6 | S3未設定時のグレースフルスキップ |
| `app/services/workspace/file_sync.py` | 6 | S3未設定時のフォールバック |
| `app/api/conversations.py` | 7 | statusパラメータのリネーム |
| `app/services/container/warm_pool.py` | 7 | デッドコード除去 |
| `tests/integration/test_phase2.py` | 8 | 修正に対応するテスト更新 |

## リスクと注意事項

1. **ステップ 3（ソケット通信）が最も複雑**: Docker-in-Docker環境でのパス解決は環境依存が大きく、
   ローカル開発環境とCI/CD環境で挙動が異なる可能性がある。ステップ3の検証は複数環境で実施すること。

2. **Claude Agent SDK (BUG-10)**: `claude-agent-sdk==0.1.23` のAPIが仕様書の記載と異なる場合、
   `workspace_agent/sdk_client.py` の大幅な書き換えが必要になる。SDKのドキュメント・ソースコードを
   事前に確認し、必要であればモック実装で代替して先に進むこと。

3. **docker-compose.yml の変更 (BUG-07)**: ソケットディレクトリのマウント方式変更は、
   既存の開発環境設定に影響する。変更前に全開発者に周知し、`.env.example` も更新すること。

4. **GCリファクタリング (ステップ5)**: OrchestratorとGCのクリーンアップ統一は、
   両者の責務境界を明確にする必要がある。過度なリファクタリングは避け、
   最小限の共通化に留めること。

## 完了基準

- [ ] `docker-compose up` でバックエンドが起動エラーなしで立ち上がる
- [ ] `/health` エンドポイントが全コンポーネント healthy で200を返す
- [ ] `workspace-base:latest` イメージがビルド・起動に成功する
- [ ] ワークスペースコンテナの HEALTHCHECK が成功する
- [ ] WarmPool プリヒートが完了し、コンテナが待機状態になる
- [ ] Unix Socket経由でワークスペースコンテナと通信が成立する
- [ ] Proxy経由でHTTP/HTTPS通信が成功する
- [ ] GCが正常にコンテナを回収しリソースをクリーンアップする
- [ ] S3未設定環境でエージェント実行がエラーなしで動作する
- [ ] 既存テスト + 新規回帰テストが全てパスする
