# ECS アーキテクチャ設計書

AWS ECS (Elastic Container Service) を使ったワークスペースコンテナ管理の設計・構成・運用ガイド。

## 目次

1. [概要](#概要)
2. [Docker / ECS モードの比較](#docker--ecs-モードの比較)
3. [コンポーネント設計](#コンポーネント設計)
4. [ECS タスク構成](#ecs-タスク構成)
5. [通信フロー](#通信フロー)
6. [Redis キー設計](#redis-キー設計)
7. [コンテナライフサイクル](#コンテナライフサイクル)
8. [障害復旧](#障害復旧)
9. [デプロイメント](#デプロイメント)
10. [設定リファレンス](#設定リファレンス)

---

## 概要

本システムは `CONTAINER_MANAGER_TYPE` 環境変数で Docker / ECS モードを切り替える。

- **Docker モード**: ローカル開発。Docker デーモンで直接コンテナを管理し、Unix Domain Socket (UDS) で通信。
- **ECS モード**: 本番 AWS 環境。ECS RunTask API でタスクを起動し、HTTP で通信。

切り替えはアプリケーション起動時に決定され、`ContainerManagerBase` 抽象基底クラスを通じて統一的に扱われる（Strategy パターン）。

## Docker / ECS モードの比較

| 項目 | Docker モード | ECS モード |
|------|-------------|-----------|
| コンテナ起動 | `aiodocker` Docker API | `aiobotocore` ECS RunTask API |
| 通信方式 | UDS (`agent.sock` / `proxy.sock`) | HTTP (`task_ip:9000`) |
| Proxy | ホスト側プロセス (UDS Listener) | サイドカーコンテナ (`:8080` / `:8081`) |
| ネットワーク | `network:none` + UDS | `awsvpc` (プライベートサブネット) |
| セキュリティ | seccomp, AppArmor, cap-drop | IAM タスクロール, SG, プライベートサブネット |
| WarmPool デフォルト | min: 2, max: 10 | min: 50, max: 120 |
| GC方式 | Docker API `list_containers` | Redis SCAN + ECS `ListTasks` 照合 |
| ログ取得 | Docker API `container.log()` | CloudWatch Logs API |
| スケーラビリティ | 単一ホスト | クラスター全体 (Fargate / EC2) |

## コンポーネント設計

### クラス階層

```
ContainerManagerBase (抽象基底: app/services/container/base.py)
├── DockerContainerLifecycle (Docker実装: app/services/container/lifecycle.py)
└── EcsContainerManager (ECS実装: app/services/container/ecs_manager.py)

ContainerOrchestrator (統括: app/services/container/orchestrator.py)
├── lifecycle: ContainerManagerBase  ← 実行時にDocker/ECSを注入
├── warm_pool: WarmPoolManager
└── redis: Redis

WarmPoolManager (app/services/container/warm_pool.py)
└── lifecycle: ContainerManagerBase  ← 同上

ContainerGarbageCollector (app/services/container/gc.py)
└── lifecycle: ContainerManagerBase  ← 同上
```

### EcsContainerManager の責務

| メソッド | 説明 |
|---------|------|
| `create_container()` | ECS RunTask → IPポーリング → Redis保存 |
| `destroy_container()` | ECS StopTask → Redis削除 |
| `is_healthy()` | DescribeTasks (status=RUNNING) + HTTP `/health` |
| `wait_for_agent_ready()` | HTTP `/health` ポーリング (早期終了検出付き) |
| `exec_in_container()` | HTTP POST `/exec` (テキスト応答) |
| `exec_in_container_binary()` | HTTP POST `/exec/binary` (バイナリ応答) |
| `list_workspace_containers()` | ListTasks + DescribeTasks (`include=['TAGS']`) |
| `get_container_logs()` | CloudWatch Logs `GetLogEvents` |
| `close()` | httpx / aiobotocore クライアント破棄 |

### Orchestrator の ECS 対応分岐

Orchestrator は `info.manager_type` フィールドで動的に分岐する。

| 処理 | Docker | ECS |
|------|--------|-----|
| `_start_proxy()` | UDS Proxy プロセス起動 | no-op (サイドカー起動済み) |
| `_stop_proxy()` | Proxy プロセス停止 | no-op (タスク停止で自動終了) |
| `_restart_proxy()` | Proxy 再起動 | no-op (コンテナ全体復旧へ) |
| `_make_agent_client()` | UDS transport 経由 | TCP HTTP 直接接続 |
| `_update_redis()` | 正引き/逆引きキーTTL更新 | 上記 + `workspace:ecs_task` キーTTL更新 |
| `_cleanup_container()` | Redis 2キー削除 | Redis 3キー削除 (ECSタスクキー含む) |
| ConnectionError 復旧 | Proxy再起動試行 → 失敗時コンテナ復旧 | 即座にコンテナ全体復旧 |
| `destroy_all()` | Proxy停止 → WarmPool drain → リスト破棄 | 同左 (Proxy停止はno-op) |

## ECS タスク構成

### タスク定義

1つの ECS タスクに2つのコンテナを配置:

```
ECS Task (awsvpc mode)
├── workspace-agent (essential: true)
│   ├── Image: <ECR>/workspace-base:latest
│   ├── Port: 9000 (HTTP)
│   ├── Env: AGENT_LISTEN_MODE=http, AGENT_HTTP_PORT=9000
│   ├── Env: HTTP_PROXY=http://127.0.0.1:8080
│   └── HealthCheck: curl http://localhost:9000/health
│
└── proxy-sidecar (essential: false)
    ├── Image: <ECR>/proxy-sidecar:latest
    ├── Port: 8080 (Forward Proxy), 8081 (Admin HTTP)
    ├── Env: PROXY_LISTEN_PORT=8080, ADMIN_PORT=8081
    └── HealthCheck: curl http://localhost:8081/health
```

### ネットワーク構成

- **awsvpc モード**: 各タスクに専用 ENI とプライベート IP を割り当て
- **プライベートサブネット**: `assignPublicIp: DISABLED`
- **セキュリティグループ**: Backend からの TCP 9000/8081 インバウンドのみ許可
- **タスク内通信**: `127.0.0.1` で workspace-agent ↔ proxy-sidecar 間通信

### タグ

RunTask 時に以下のタグを付与:

| タグキー | 値 | 用途 |
|---------|-----|------|
| `workspace` | `"true"` | ワークスペースタスクの識別 |
| `workspace.container_id` | `ws-{uuid12}` | コンテナID |
| `workspace.conversation_id` | `{conversation_id}` | 会話ID紐付け |

## 通信フロー

### リクエスト実行フロー

```
Client → FastAPI Backend
  → orchestrator.get_or_create(conversation_id)
    → warm_pool.acquire()
      → Redis LPOP (WarmPoolキュー)
      → ecs_manager.is_healthy(check_agent=True)
        → ECS DescribeTasks (status=RUNNING確認)
        → HTTP GET http://{task_ip}:9000/health
    → Redis保存 (container, reverse, ecs_task キー)
  → orchestrator.execute()
    → httpx.AsyncClient (TCP)
    → POST http://{task_ip}:9000/execute
    → SSE ストリーム中継
    → Redis更新 (status=IDLE, TTLリフレッシュ)
```

### workspace-agent エンドポイント

| パス | メソッド | 説明 |
|------|---------|------|
| `/health` | GET | ヘルスチェック |
| `/execute` | POST | エージェント実行 (SSE ストリーム) |
| `/exec` | POST | コマンド実行 (テキスト応答) |
| `/exec/binary` | POST | コマンド実行 (バイナリ応答) |

### Proxy サイドカー エンドポイント

| ポート | パス | 説明 |
|--------|------|------|
| 8080 | `*` | Forward Proxy (SigV4署名注入 + ドメインホワイトリスト) |
| 8081 | `/health` | ヘルスチェック |
| 8081 | `/admin/update-rules` | MCP ヘッダールール更新 |

## Redis キー設計

ECS モードでは3種類の Redis キーでコンテナ状態を管理する。

### キー一覧

| キー | 型 | TTL | 用途 |
|------|-----|-----|------|
| `workspace:container:{conversation_id}` | Hash | 3600s | コンテナ情報 (正引き) |
| `workspace:container_reverse:{container_id}` | String | 3600s | container_id → conversation_id (逆引き) |
| `workspace:ecs_task:{container_id}` | String | 3600s | container_id → task_arn (ECS固有) |
| `workspace:warm_pool` | List | - | WarmPool コンテナIDキュー |
| `workspace:warm_pool_info:{container_id}` | Hash | 1800s | WarmPool コンテナ情報 |

### TTL 管理

- 全キーの TTL は `CONTAINER_TTL_SECONDS` (3600s) に統一
- `_update_redis()` で3キーの TTL を同時にリフレッシュ
- TTL 切れ → GC が検出 → `_graceful_destroy()` で全キー削除

### キーのライフサイクル

```
create_container()  → ecs_task キー作成 (TTL: 3600s)
_save_to_redis()    → container + reverse キー作成 (TTL: 3600s)
_update_redis()     → 3キー全ての TTL リフレッシュ
_cleanup_container()→ 3キー全て削除
destroy_container() → ecs_task キー削除 (冪等: 直接呼び出し対応)
```

## コンテナライフサイクル

### 起動シーケンス

```
1. ECS RunTask API呼び出し
   └── タスクタグ: workspace, container_id, conversation_id
   └── capacityProviderStrategy (EC2モード時)

2. IPアドレスポーリング (_wait_for_task_ip)
   └── DescribeTasks → ENI → privateIPv4Address
   └── 1回のAPIコールでIP取得+ステータス確認
   └── タイムアウト: 120秒, ポーリング間隔: 2秒

3. Redis保存
   └── workspace:ecs_task:{container_id} = task_arn

4. エージェント起動待ち (wait_for_agent_ready)
   └── HTTP GET /health ポーリング (間隔: 0.5秒)
   └── 5ポーリングごとにタスク生存確認
   └── 早期終了検出: STOPPED/DEPROVISIONING → ログ取得 → return False
```

### シャットダウンシーケンス (destroy_all)

```
1. 全Proxy停止 (ECS: no-op)
2. WarmPool ドレイン (先行: 二重破棄防止)
   └── Redis LPOP → WarmPool情報削除 → ECS StopTask
3. 残存コンテナ破棄
   └── ListTasks → DescribeTasks (include=['TAGS'])
   └── タグからcontainer_id抽出 → StopTask
```

### GC (ガベージコレクション)

```
通常サイクル:
  Redis SCAN workspace:container:* → 各コンテナの TTL/ステータス確認
  → 期限切れ → graceful_destroy (status→draining → StopTask → Redis全削除)

5サイクルごと:
  ECS ListTasks + DescribeTasks → Redis照合
  → Redis に記録がないタスク → 孤立タスクとして StopTask
```

## 障害復旧

### ConnectionError 発生時

| モード | 復旧フロー |
|--------|-----------|
| Docker | Proxy再起動試行 → 成功: `container_recovered` 送出 / 失敗: コンテナ全体復旧 |
| ECS | コンテナ全体復旧に直接進む (Proxyサイドカーの単体再起動は不可) |

### コンテナ全体復旧フロー

```
1. _cleanup_container(info)
   └── StopTask + Redis 全キー削除
2. get_or_create(conversation_id)
   └── WarmPool から新コンテナ取得
3. container_recovered イベント送出
```

### タイムアウト復旧

エージェント実行タイムアウト (デフォルト 600s) 発生時:
1. エラーイベント送出
2. 旧コンテナをクリーンアップ
3. 新コンテナを取得
4. 次回リクエストに備える

## デプロイメント

### コンテナイメージ

| イメージ | Dockerfile | 説明 |
|---------|-----------|------|
| `workspace-base` | `workspace-base/Dockerfile` | workspace-agent + SDK + ツール |
| `proxy-sidecar` | `workspace-base/Dockerfile.proxy-sidecar` | Credential Injection Proxy |

### ビルド

```bash
# workspace-agent イメージ
docker build -t <ECR>/workspace-base:latest -f workspace-base/Dockerfile .

# proxy-sidecar イメージ
docker build -t <ECR>/proxy-sidecar:latest -f workspace-base/Dockerfile.proxy-sidecar .
```

### ECS タスク定義の要点

```json
{
  "family": "workspace-agent",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "containerDefinitions": [
    {
      "name": "workspace-agent",
      "image": "<ECR>/workspace-base:latest",
      "essential": true,
      "portMappings": [{"containerPort": 9000}],
      "environment": [
        {"name": "AGENT_LISTEN_MODE", "value": "http"},
        {"name": "AGENT_HTTP_PORT", "value": "9000"},
        {"name": "HTTP_PROXY", "value": "http://127.0.0.1:8080"},
        {"name": "HTTPS_PROXY", "value": "http://127.0.0.1:8080"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/workspace-agent",
          "awslogs-region": "<region>",
          "awslogs-stream-prefix": "ecs"
        }
      }
    },
    {
      "name": "proxy-sidecar",
      "image": "<ECR>/proxy-sidecar:latest",
      "essential": false,
      "portMappings": [
        {"containerPort": 8080},
        {"containerPort": 8081}
      ]
    }
  ]
}
```

### IAM 要件

Backend の実行ロールに以下の権限が必要:

- `ecs:RunTask` / `ecs:StopTask` / `ecs:DescribeTasks` / `ecs:ListTasks`
- `iam:PassRole` (タスクロール / タスク実行ロールの PassRole)
- `logs:GetLogEvents` (デバッグログ取得)

## 設定リファレンス

### 環境変数

| 変数名 | 必須 | デフォルト | 説明 |
|--------|------|----------|------|
| `CONTAINER_MANAGER_TYPE` | - | `docker` | `ecs` で ECS モード有効化 |
| `ECS_CLUSTER` | ECS時必須 | - | ECS クラスター名 |
| `ECS_TASK_DEFINITION` | ECS時必須 | - | タスク定義 (family, ARN, family:rev) |
| `ECS_SUBNETS` | ECS時必須 | - | サブネットID (カンマ区切り) |
| `ECS_SECURITY_GROUPS` | ECS時必須 | - | SG ID (カンマ区切り) |
| `ECS_CAPACITY_PROVIDER` | - | - | CP名 (省略で Fargate) |
| `ECS_AGENT_PORT` | - | `9000` | Agent HTTP ポート |
| `ECS_PROXY_ADMIN_PORT` | - | `8081` | Proxy Admin ポート |
| `ECS_RUN_TASK_CONCURRENCY` | - | `10` | RunTask 同時実行上限 |
| `ECS_WARM_POOL_MIN_SIZE` | - | `50` | WarmPool 最小サイズ |
| `ECS_WARM_POOL_MAX_SIZE` | - | `120` | WarmPool 最大サイズ |

### 内部定数

| 定数 | 値 | ファイル | 説明 |
|------|-----|---------|------|
| `_TASK_IP_POLL_INTERVAL` | 2.0s | ecs_manager.py | IP ポーリング間隔 |
| `_TASK_IP_POLL_TIMEOUT` | 120.0s | ecs_manager.py | IP ポーリングタイムアウト |
| `CONTAINER_TTL_SECONDS` | 3600s | config.py | Redis キー TTL |
| `WARM_POOL_TTL_SECONDS` | 1800s | config.py | WarmPool 情報 TTL |
