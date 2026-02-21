# AWS ECS移行 実装プラン

## 概要
aiodocker単一ホスト構成 → Backend(ECS Service) + Workspace(ECS RunTask) 分離構成へ移行。
`CONTAINER_MANAGER_TYPE=docker|ecs` で切替。ローカル開発(Docker)は既存動作を完全維持。

---

## Phase 0: ABC抽象化 + ContainerInfo拡張（純粋リファクタリング）

**ゴール**: ランタイム動作を一切変えず、Strategy Patternの土台を作る

### 0.1 `ContainerManagerBase` ABC新規作成
- **新規**: `app/services/container/base.py`
- 抽象メソッド: `create_container`, `destroy_container`, `is_healthy`, `list_workspace_containers`, `wait_for_agent_ready`, `exec_in_container`, `exec_in_container_binary`, `get_container_logs`
- `wait_for_agent_ready`のシグネチャ: `(container_info: ContainerInfo, timeout)` に変更（UDS/HTTP両対応）
- `get_container_logs`を公開メソッドに昇格（orchestrator.pyの漏れ抽象化を修正）

### 0.2 既存クラスのリネーム
- **変更**: `app/services/container/lifecycle.py`
  - L19: `ContainerLifecycleManager` → `DockerContainerManager(ContainerManagerBase)`
  - L248: `_get_container_logs` → `get_container_logs`（公開化）
  - 末尾に `ContainerLifecycleManager = DockerContainerManager` エイリアス追加

### 0.3 ContainerInfo拡張
- **変更**: `app/services/container/models.py`
  - 新フィールド追加: `task_arn: str = ""`, `task_ip: str = ""`, `manager_type: str = "docker"`
  - `from_redis_hash`: `data["key"]` → `data.get("key", "")` に変更（後方互換）
  - `to_redis_hash`: 新フィールド追加

### 0.4 型ヒント更新（全消費者）
| ファイル | 変更内容 |
|---------|---------|
| `orchestrator.py` L46,66 | `ContainerLifecycleManager` → `ContainerManagerBase` |
| `warm_pool.py` L28,42 | 同上 |
| `gc.py` L19,31 | 同上 |
| `file_sync.py` L22,57 | 同上 |
| `orchestrator.py` L329-336 | `self.lifecycle.docker.containers.get()` → `self.lifecycle.get_container_logs()` |

### 0.5 設定追加
- **変更**: `app/config.py` L177以降に追加:
```
container_manager_type: str = "docker"
ecs_cluster_name: str = ""
ecs_task_definition: str = ""
ecs_subnets: str = ""  # カンマ区切り
ecs_security_group: str = ""
ecs_capacity_provider: str = ""
ecs_workspace_agent_port: int = 9000
ecs_proxy_admin_port: int = 8081
```

### 検証
- 全既存テストがパス
- `ContainerInfo.from_redis_hash`が旧フォーマットRedisデータで動作
- ランタイム動作ゼロ変更

### リスク
- `warm_pool.py` L125-127の `wait_for_agent_ready` シグネチャ変更漏れ → `info.agent_socket` → `info` に変更必要

---

## Phase 1: workspace_agent HTTP化（/exec, デュアルモード）

**ゴール**: workspace_agentにHTTPリッスン + /exec エンドポイントを追加

### 1.1 /exec, /exec/binary エンドポイント追加
- **変更**: `workspace_agent/main.py` L50以降
- `POST /exec`: `asyncio.create_subprocess_exec` → JSON `{exit_code, output}`
- `POST /exec/binary`: stdout → `application/octet-stream`, exit_code → `X-Exit-Code`ヘッダー
- **変更**: `workspace_agent/models.py` に `ExecRequest(cmd: list[str], timeout: int = 60)` 追加

### 1.2 デュアルモード起動
- **変更**: `workspace_agent/main.py` L138-140
- 環境変数 `AGENT_LISTEN_MODE=uds|http` で切替
- UDS: 既存動作（`uvicorn.run(app, uds=AGENT_SOCKET)`）
- HTTP: `uvicorn.run(app, host="0.0.0.0", port=9000)`

### 1.3 entrypoint.sh更新
- **変更**: `workspace-base/entrypoint.sh`
- `AGENT_LISTEN_MODE=http` の場合socat不要（proxyはサイドカー）
- `AGENT_LISTEN_MODE=uds`（デフォルト）は既存動作維持

### 1.4 Dockerfile HEALTHCHECK更新
- **変更**: `workspace-base/Dockerfile` L36-37
- HTTP mode: `curl -sf http://localhost:9000/health`
- UDS mode: 既存（`curl --unix-socket`）

### 検証
- `AGENT_LISTEN_MODE=uds`で既存動作
- `AGENT_LISTEN_MODE=http`でTCPリッスン + `/exec` + `/health` 動作
- `/exec/binary`のバイナリ忠実度確認

### リスク
- `/exec`は任意コマンド実行可能 → SGでBackendからのみアクセス許可 + 共有シークレット検討
- 大容量バイナリレスポンスのメモリ問題 → 現用途(ファイル読取)では許容範囲

---

## Phase 2: EcsContainerManager実装

**ゴール**: ABC準拠のECS実装を作成

### 2.1 新規ファイル作成
- **新規**: `app/services/container/ecs_manager.py` (~350行)
- `EcsContainerManager(ContainerManagerBase)` を実装

### 2.2 主要メソッド設計

**`create_container`**: `ecs.run_task()` → `_wait_for_task_ip()` → ContainerInfo返却
- `agent_socket`フィールドにHTTP URL格納 (`http://{task_ip}:9000`)
- `proxy_socket`フィールドにadmin URL格納 (`http://{task_ip}:8081`)
- タグ: `workspace=true`, `workspace.container_id`, `workspace.conversation_id`
- `enableExecuteCommand=False`, `taskRoleArn=null`

**`destroy_container`**: Redis→task_arn解決 → `ecs.stop_task()`

**`is_healthy`**: `ecs.describe_tasks` + オプションHTTPヘルスチェック

**`wait_for_agent_ready`**: `http://{task_ip}:9000/health` ポーリング（0.5秒間隔）

**`exec_in_container`**: `http://{task_ip}:9000/exec` POST

**`exec_in_container_binary`**: `http://{task_ip}:9000/exec/binary` POST

**`list_workspace_containers`**: ECS ListTasks → DescribeTasks → Docker API互換dict合成（GC互換）

**`get_container_logs`**: CloudWatch Logs API

### 2.3 内部ヘルパー
- `_wait_for_task_ip(task_arn, timeout=120)`: ENI IP取得ポーリング
- `_resolve_task_arn(container_id)`: Redis逆引き → ECSタグフォールバック
- `_resolve_task_ip(container_id)`: Redis → ContainerInfo → task_ip
- Redis追加キー: `workspace:ecs_task:{container_id}` → task_arn

### 2.4 依存追加
- `requirements.txt`: `aiobotocore>=2.12.0` 追加

### 検証
- moto/botocore stubberでユニットテスト
- Docker側テストに影響なし

### リスク
- **ENI取得遅延**: 10-30秒（Docker <1秒）→ WarmPool大きめに設定
- **ECS API レート制限**: RunTask ~1req/s → WarmPool補充にレートリミット必要
- **ENI Trunking未設定**: デフォルトENI数制限(3-15)でタスク密度低下 → `ECS_ENABLE_TASK_ENI_TRUNKING=true` 必須
- **run_task失敗**: `response['failures']` 配列チェック必要（例外ではなく配列で返る）

---

## Phase 3: Orchestrator通信抽象化（UDS→HTTP）

**ゴール**: execute()のトランスポート選択をContainerInfoベースに切替

### 3.1 トランスポートファクトリ追加
- **変更**: `orchestrator.py` L140-290
- `_get_agent_client_and_url(info)` ヘルパー追加
  - `info.manager_type == "ecs"` → 直接HTTP (`httpx.AsyncClient`)
  - else → UDS (`httpx.AsyncHTTPTransport(uds=info.agent_socket)`)

### 3.2 execute()リファクタリング
- L167-179: ハードコードUDS → `_get_agent_client_and_url` 経由
- エラーハンドリング(L181-281): 既存のクラッシュ復旧ロジックは両モードで動作（ConnectionError→再作成）

### 検証
- Docker: UDS通信でSSEストリーミング動作
- ECS: HTTP通信でSSEストリーミング動作（ローカルHTTPサーバーでモック可）

### リスク
- HTTP接続拒否: ENI未接続時 → `wait_for_agent_ready`でカバー済み
- ネットワーク分断: タイムアウトベース復旧(既存ロジック)で対応

---

## Phase 4: Proxyサイドカー化

**ゴール**: ECSモードでProxyをサイドカーコンテナとして動作させる

### 4.1 Proxy管理のモード分岐
- **変更**: `orchestrator.py` L338-388
- `_start_proxy`: ECSモード → no-op（サイドカー自動起動）+ 初期設定プッシュ
- `_stop_proxy`: ECSモード → no-op（タスク停止時に自動停止）
- `_restart_proxy`: ECSモード → タスク再作成にフォールバック

### 4.2 MCP header更新のasync化 **【破壊的変更】**
- **変更**: `orchestrator.py` L367
  - `def update_mcp_header_rules` → `async def update_mcp_header_rules`
  - ECS: `http://{task_ip}:8081/admin/update-rules` にHTTP POST
  - Docker: 既存のインメモリdict更新
- **変更**: `execute_service.py` L599
  - `self.orchestrator.update_mcp_header_rules(...)` → `await self.orchestrator.update_mcp_header_rules(...)`
- **変更**: `execute_service.py` L544
  - `def _extract_mcp_headers_to_proxy` → `async def _extract_mcp_headers_to_proxy`
- **変更**: `execute_service.py` L443
  - `self._extract_mcp_headers_to_proxy(...)` → `await self._extract_mcp_headers_to_proxy(...)`

### 4.3 Proxy admin HTTPサーバー追加
- **変更**: `app/services/proxy/credential_proxy.py`
- `start_admin_server(port=8081)`: TCP HTTPサーバー起動
  - `POST /admin/update-rules`: MCPルール更新
  - `GET /health`: ヘルスチェック
  - `POST /admin/config`: 初期設定（AWS認証情報、ホワイトリスト）プッシュ

### 4.4 Proxyサイドカー用Dockerfile
- **新規**: `workspace-base/Dockerfile.proxy-sidecar`（またはマルチステージ）
- 既存`CredentialInjectionProxy`をHTTPモードで起動するエントリポイント

### 4.5 ECSタスク定義（参照用）
```json
containerDefinitions:
  - name: workspace-agent (port 9000)
    env: AGENT_LISTEN_MODE=http, ANTHROPIC_BEDROCK_BASE_URL=http://localhost:8080
  - name: credential-proxy (port 8080, admin 8081)
    env: PROXY_MODE=http, AWS credentials from Secrets Manager
networkMode: awsvpc
taskRoleArn: null
```

### 検証
- Docker: インプロセスProxy起動/停止、MCPルール更新が既存通り動作
- ECS: admin HTTP経由でMCPルール更新成功
- async化によるコール元の挙動確認

### リスク
- **async化の波及**: `_extract_mcp_headers_to_proxy`のasync化が呼び出し元チェーンに波及
- **MCP更新タイミング**: タスク起動→ルールプッシュ間のウィンドウ → execute前にプッシュ済みなので問題なし
- **admin port認証なし**: SGでBackend SGからのみ許可 + 共有シークレット検討

---

## Phase 5: GC適応

**ゴール**: GCをDocker/ECS両モードで動作させる

### 5.1 GC収集ロジック分岐
- **変更**: `gc.py` L75-148
- `_collect()` → `_collect_docker()` (既存) / `_collect_ecs()` (新規)
- ECSモード: Redis SCAN `workspace:container:*` ベース
  - TTL/ステータスチェック → `_should_destroy` → `_graceful_destroy`
- Docker互換dict合成(`list_workspace_containers`)は維持するが、Redis直接スキャンの方が効率的

### 5.2 ECS孤立タスク検出
- ECS `ListTasks` → Redis照合 → 未登録タスクを停止
- 高コスト → GCサイクル5回に1回のみ実行

### 5.3 proxy_stop_callback
- ECSモード: no-op（サイドカー）→ コールバック呼び出しは安全（_stop_proxyがno-opのため）

### 検証
- Docker: GC既存動作維持
- ECS: Redis SCANでTTL超過コンテナ検出・破棄

### リスク
- Redis SCANのパフォーマンス → `count=100`でページング
- ECS孤立検出のAPI呼び出しコスト → 頻度制限で対応

---

## Phase 6: Lifespan配線

**ゴール**: Strategy PatternをLifespanで配線

### 6.1 `_init_container_stack`更新
- **変更**: `lifespan.py` L105-156
```python
if settings.container_manager_type == "ecs":
    session = aiobotocore.AioSession()
    lifecycle = EcsContainerManager(session, settings)
    docker_client = None
else:
    docker_client = aiodocker.Docker(url=settings.docker_socket_path)
    lifecycle = DockerContainerManager(docker_client)
```

### 6.2 `_shutdown_container_stack`更新
- **変更**: `lifespan.py` L180-209
- `docker_client`が`None`（ECSモード）の場合のclose()スキップ

### 検証
- `CONTAINER_MANAGER_TYPE=docker`（デフォルト）: 完全に既存動作
- `CONTAINER_MANAGER_TYPE=ecs`: EcsContainerManager初期化
- 両モードでクリーンシャットダウン

---

## Phase 7: セキュリティ強化（IaC/運用）

### 7.1 セキュリティマッピング
| Docker | ECS |
|--------|-----|
| NetworkMode=none | SG: Backend→9000, Egress→443のみ |
| CapDrop=ALL | `linuxParameters.capabilities.drop=ALL` |
| ReadonlyRootfs | `readonlyRootFilesystem: true` |
| seccomp | カスタムAMI daemon-wide設定（ECS #1782） |
| AppArmor | 非対応 → seccompのみ |
| PidsLimit | カスタムAMI sysctl |

### 7.2 ECScape緩和
- `taskRoleArn=null`
- `ECS_AWSVPC_BLOCK_IMDS=true`（ECSエージェント設定）
- Proxyドメインホワイトリスト（169.254.169.254は拒否）

### 7.3 インフラ構成（IaC）
- ECSクラスター + EC2 Capacity Provider
- カスタムAMI: ENI Trunking有効、seccompデフォルト設定
- VPC: Private Subnet + NAT Gateway
- ECR: workspace-baseイメージ、proxy-sidecarイメージ
- Secrets Manager: AWS認証情報

---

## Phase 8: 統合テスト + ロールアウト

### テスト戦略
| Phase | テスト種別 | 対象 |
|-------|-----------|------|
| 0 | Unit | ABC契約、Redis往復、エイリアス互換 |
| 1 | Unit+E2E | /exec応答、バイナリ忠実度 |
| 2 | Unit(moto) | RunTask/StopTask/DescribeTasks |
| 3 | Integration | SSEストリーミング(HTTP) |
| 4 | Integration | MCPルールHTTPプッシュ |
| 5 | Integration | Redis SCAN GC |
| 6 | Integration | Lifespan起動/停止 |
| 7 | Security | IMDS遮断、SG検証、ポートスキャン |

### ロールアウト
1. Staging: `CONTAINER_MANAGER_TYPE=ecs`でフルテスト
2. Canary: 5%トラフィック
3. 段階拡大: 25% → 50% → 100%
4. ロールバック条件: エラー率>1% or P99レイテンシ>2x

---

## 全変更ファイル一覧

| Phase | ファイル | 種別 |
|-------|---------|------|
| 0 | `app/services/container/base.py` | 新規 |
| 0 | `app/services/container/lifecycle.py` | 変更(リネーム+ABC継承) |
| 0 | `app/services/container/models.py` | 変更(フィールド追加+.get()) |
| 0 | `app/services/container/orchestrator.py` | 変更(型ヒント+ログ抽象化修正) |
| 0 | `app/services/container/warm_pool.py` | 変更(型ヒント+シグネチャ) |
| 0 | `app/services/container/gc.py` | 変更(型ヒント) |
| 0 | `app/services/workspace/file_sync.py` | 変更(型ヒント) |
| 0 | `app/config.py` | 変更(ECS設定追加) |
| 1 | `workspace_agent/main.py` | 変更(/exec+デュアルモード) |
| 1 | `workspace_agent/models.py` | 変更(ExecRequest) |
| 1 | `workspace-base/entrypoint.sh` | 変更(モード分岐) |
| 1 | `workspace-base/Dockerfile` | 変更(HEALTHCHECK) |
| 2 | `app/services/container/ecs_manager.py` | 新規(~350行) |
| 2 | `requirements.txt` | 変更(aiobotocore) |
| 3 | `app/services/container/orchestrator.py` | 変更(トランスポート選択) |
| 4 | `app/services/container/orchestrator.py` | 変更(Proxy管理分岐) |
| 4 | `app/services/proxy/credential_proxy.py` | 変更(admin HTTP追加) |
| 4 | `app/services/execute_service.py` | 変更(async化3箇所) |
| 4 | `workspace-base/Dockerfile.proxy-sidecar` | 新規 |
| 5 | `app/services/container/gc.py` | 変更(ECS GC) |
| 6 | `app/core/lifespan.py` | 変更(ファクトリ配線) |

---

## リスクレジスタ

### 高リスク
1. **ENI枯渇**: Trunking未設定で密度低下 → 監視+Trunking必須
2. **RunTask遅延**: 10-30秒 → WarmPool増量で緩和
3. **from_redis_hash後方互換**: `.get()`移行漏れ → Phase 0で全箇所修正

### 中リスク
4. **exec endpoint セキュリティ**: SG + 共有シークレット
5. **update_mcp_header_rules async化**: 呼び出し元3箇所の変更漏れ
6. **CloudWatch Logs遅延**: ログ即時取得不可 → リトライ付きget_container_logs

### 低リスク
7. **ECS API レートリミット**: WarmPool補充にバックオフ
8. **admin port認証なし**: SGで制限
