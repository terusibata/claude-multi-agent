# AWS ECS移行 実装プラン

## 概要
aiodocker単一ホスト → Backend(ECS Service) + Workspace(ECS RunTask)分離。
`CONTAINER_MANAGER_TYPE=docker|ecs`切替。ローカルDocker開発は完全維持。

## リスク対策の実現可能性（調査済み）

| リスク | 可能性 | 対策 | 備考 |
|--------|-------|------|------|
| ENI枯渇 | 85% | Trunking有効(c5.xl=120ENI) | 50-100同時に十分。IaC必須 |
| RunTask遅延(10-30s) | 90% | WarmPool増量(min50/max120) | LPOP原子性でレース条件なし。現行min2/max10は不足 |
| from_redis_hash互換 | **要対策** | Phase分割で先行修正 | 現行は全`data["key"]`直接アクセス。ローリングデプロイで即KeyError |
| async化波及 | 95% | 呼出元1箇所のみ(3行変更) | Phase 0で先行変換 |

---

## Phase 0-a: ブロッカー解消（防御的デシリアライズ + async先行）

### 0-a.1 `from_redis_hash`防御化 **【最優先・単独デプロイ】**
- `models.py` L44-55: 全`data["key"]` → `data.get("key", default)` に変換
- 新フィールドはまだ追加しない（既存7フィールドのみ）
- WarmPool TTL=30分、コンテナTTL=60分の残存データに対応

### 0-a.2 `update_mcp_header_rules` async先行変換
- `orchestrator.py` L367: `def` → `async def`（中身変更なし）
- `execute_service.py` L544: `def _extract_mcp_headers_to_proxy` → `async def`
- `execute_service.py` L443,599: `await`追加
- Docker動作に影響なし（async内の同期処理）

### 0-a.3 ECS用WarmPoolサイズ設定
- `config.py`: `ecs_warm_pool_min_size: int = 50`, `ecs_warm_pool_max_size: int = 120`
- `warm_pool.py`: `_reload_config()`でmanager_type応じたサイズ切替 + Semaphore(ECS API制限対応)

### 検証: 旧Redis読込OK、async化でDocker動作変化なし、全テストパス

---

## Phase 0-b: ABC抽象化 + ContainerInfo拡張

### 0-b.1 ABC新規作成
- **新規** `app/services/container/base.py`: `ContainerManagerBase` ABC
- 抽象メソッド8つ: create/destroy/is_healthy/list/wait_for_agent_ready/exec/exec_binary/get_logs
- `wait_for_agent_ready(container_info: ContainerInfo, timeout)`シグネチャ

### 0-b.2 クラスリネーム
- `lifecycle.py` L19: `ContainerLifecycleManager` → `DockerContainerManager(ContainerManagerBase)`
- `_get_container_logs` → `get_container_logs`（公開化）
- 末尾エイリアス: `ContainerLifecycleManager = DockerContainerManager`

### 0-b.3 ContainerInfo新フィールド
- `models.py`: `task_arn=""`, `task_ip=""`, `manager_type="docker"` 追加
- `to_redis_hash`/`from_redis_hash`: 0-a.1で`.get()`化済み → `.get("task_arn","")`追加のみ

### 0-b.4 型ヒント一括更新
- `orchestrator.py` L46,66 / `warm_pool.py` L28,42,125-127 / `gc.py` L19,31 / `file_sync.py` L22,57
- `orchestrator.py` L329-336: `self.lifecycle.docker.containers.get()` → `self.lifecycle.get_container_logs()`

### 0-b.5 設定追加
- `config.py`: `container_manager_type="docker"`, ECS設定(cluster/task_def/subnets/sg/capacity_provider/ports)

### 検証: 全テストパス、エイリアスで旧import互換、Redis往復(旧+新データ)

---

## Phase 1: workspace_agent HTTP化

### 1.1 /exec, /exec/binary エンドポイント
- `workspace_agent/main.py`: `POST /exec` → JSON{exit_code, output}
- `POST /exec/binary` → octet-stream + X-Exit-Codeヘッダー
- `workspace_agent/models.py`: `ExecRequest(cmd: list[str], timeout: int = 60)`

### 1.2 デュアルモード起動
- `workspace_agent/main.py` L138: `AGENT_LISTEN_MODE=uds|http`で切替
- `workspace-base/entrypoint.sh`: httpモード時socat不要
- `workspace-base/Dockerfile`: HEALTHCHECK条件分岐

### 検証: UDS既存動作、HTTP /exec + /health、バイナリ忠実度
### リスク: /exec任意実行 → SG制限 + 共有シークレット検討

---

## Phase 2: EcsContainerManager実装

### 2.1 新規 `app/services/container/ecs_manager.py` (~350行)
- `create_container`: run_task → _wait_for_task_ip → ContainerInfo(agent_socket=HTTP URL)
- `destroy_container`: Redis→task_arn → stop_task
- `is_healthy`: describe_tasks + オプションHTTP /health
- `wait_for_agent_ready`: http://{task_ip}:9000/health ポーリング
- `exec_in_container`/`_binary`: http://{task_ip}:9000/exec POST
- `list_workspace_containers`: ListTasks→DescribeTasks→Docker互換dict合成
- `get_container_logs`: CloudWatch Logs API
- ヘルパー: `_wait_for_task_ip`, `_resolve_task_arn`(Redis逆引き), Redis追加キー

### 2.2 依存: `requirements.txt` に `aiobotocore>=2.12.0`

### 検証: moto/stubberでユニットテスト、Docker側無影響
### リスク: ENI遅延→WarmPool増量、API制限→Semaphore、run_task `failures`配列チェック必須

---

## Phase 3: Orchestrator UDS→HTTP切替

### 3.1 トランスポートファクトリ
- `orchestrator.py`: `_get_agent_client_and_url(info)`追加
  - `info.manager_type=="ecs"` → httpx.AsyncClient直接
  - else → httpx.AsyncHTTPTransport(uds=)

### 3.2 execute()リファクタリング
- L167-179: ハードコードUDS → ファクトリ経由
- クラッシュ復旧(L181-281): ConnectionError→再作成は両モードで動作

### 検証: Docker UDS SSE動作、ECS HTTP SSE動作

---

## Phase 4: Proxyサイドカー化

### 4.1 Proxy管理モード分岐
- `orchestrator.py` L338-388:
  - `_start_proxy`: ECS→no-op(+初期設定プッシュ)
  - `_stop_proxy`: ECS→no-op
  - `update_mcp_header_rules`: ECS→HTTP POST /admin/update-rules（0-a.2でasync化済み）

### 4.2 Proxy admin HTTPサーバー
- `credential_proxy.py`: `start_admin_server(port=8081)`
  - POST /admin/update-rules, POST /admin/config, GET /health

### 4.3 サイドカーDockerfile + ECSタスク定義
- 新規 `workspace-base/Dockerfile.proxy-sidecar`
- タスク定義: workspace-agent(:9000) + credential-proxy(:8080, admin:8081), awsvpc, taskRoleArn=null

### 検証: Docker既存Proxy動作、ECS admin HTTP MCPルール更新

---

## Phase 5: GC適応

- `gc.py` L75-148: `_collect()` → Docker/ECS分岐
- ECS: Redis SCAN `workspace:container:*`ベース
- 孤立タスク検出: ListTasks→Redis照合（GC5サイクルに1回）
- proxy_stop_callback: ECS=no-op（安全）

---

## Phase 6: Lifespan配線

- `lifespan.py` L105-156: `container_manager_type`でDockerContainerManager/EcsContainerManager切替
- L180-209: docker_client=None(ECS時)のclose()スキップ

---

## Phase 7: セキュリティ(IaC)

| Docker | ECS対応 |
|--------|---------|
| NetworkMode=none | SG: Backend→9000, Egress→443 |
| CapDrop=ALL | linuxParameters.capabilities.drop=ALL |
| ReadonlyRootfs | readonlyRootFilesystem: true |
| seccomp | カスタムAMI daemon-wide（ECS #1782） |
| AppArmor | 非対応→seccompのみ |

ECScape緩和: taskRoleArn=null, ECS_AWSVPC_BLOCK_IMDS=true, Proxyホワイトリスト
インフラ: ECSクラスター, EC2 Capacity Provider, カスタムAMI(Trunking+seccomp), Private Subnet+NAT, ECR, Secrets Manager

## Phase 8: 統合テスト + ロールアウト

Staging→Canary(5%)→25%→50%→100%。ロールバック: エラー率>1% or P99>2x。

---

## 全変更ファイル

| Phase | ファイル | 種別 |
|-------|---------|------|
| 0-a | models.py, orchestrator.py, execute_service.py, config.py, warm_pool.py | 変更 |
| 0-b | base.py(新規), lifecycle.py, models.py, orchestrator.py, warm_pool.py, gc.py, file_sync.py, config.py | 変更 |
| 1 | workspace_agent/main.py, models.py, entrypoint.sh, Dockerfile | 変更 |
| 2 | ecs_manager.py(新規), requirements.txt | 新規+変更 |
| 3 | orchestrator.py | 変更 |
| 4 | orchestrator.py, credential_proxy.py, Dockerfile.proxy-sidecar(新規) | 変更+新規 |
| 5 | gc.py | 変更 |
| 6 | lifespan.py | 変更 |
