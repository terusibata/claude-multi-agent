# ECS on EC2 移行調査 — アーキテクチャ設計ドキュメント

## Context

現行システムは **aiodocker（Docker API直接操作）** による単一ホスト設計で、Claude Agent SDK のワークスペースコンテナを管理している。以下の限界を解決するため、**ECS on EC2** への移行可能性を調査する。

- **スケール上限**: 単一ホストのCPU/MEM物理制約で同時50-100コンテナ
- **単一障害点**: ホスト障害＝全コンテナ喪失
- **運用負荷**: Docker daemon の直接管理、手動スケーリング

**要件**: ローカル開発はdocker-compose、AWS上でステージング/本番環境を構築可能とすること。

---

## 1. 現行アーキテクチャの要約

### 1.1 コンテナ管理フロー
```
API Request → Orchestrator → Redis検索
  ├─ 既存コンテナ発見 → TTLリセット → UDS経由でリクエスト転送
  └─ なし → WarmPool.acquire() → Proxy起動 → S3同期 → Redis記録
```

### 1.2 通信パターン（UDS）
```
Backend (Host)
  httpx.AsyncHTTPTransport(uds=agent_socket)
  └─→ /var/run/workspace-sockets/{container_id}/agent.sock
       │ (Bind Mount)
       ▼
Container (NetworkMode:none)
  workspace_agent (FastAPI on UDS)
  socat TCP:8080 ↔ UNIX:proxy.sock
       │ (Bind Mount back to Host)
       ▼
  CredentialInjectionProxy (Host側asyncio.start_unix_server)
  ├→ Bedrock (SigV4署名注入)
  ├→ MCP (認証ヘッダー注入)
  └→ External (ドメインホワイトリスト)
```

### 1.3 セキュリティレイヤー
| レイヤー | 現行実装 | 設定ファイル |
|---------|---------|------------|
| ネットワーク | NetworkMode:none | `config.py:95` |
| Capabilities | CapDrop:ALL + 最小限Add | `config.py:101-102` |
| ファイルシステム | ReadonlyRootfs + tmpfs | `config.py:105-118` |
| Syscalls | seccompプロファイル | `deployment/seccomp/workspace-seccomp.json` |
| AppArmor | オプション | `deployment/apparmor/workspace-container` |
| 権限昇格 | no-new-privileges | `config.py:59` |
| ユーザー | 1000:1000 (非root) | `config.py:89` |
| IPC | private | `config.py:106` |
| リソース | CPU 2core, MEM 2GB, PID 256 | `config.py:96-100` |
| 認証情報 | Proxy経由注入（コンテナに直接渡さない） | `credential_proxy.py` |

### 1.4 重要ファイル
| コンポーネント | パス |
|--------------|------|
| Orchestrator | `app/services/container/orchestrator.py` |
| Lifecycle | `app/services/container/lifecycle.py` |
| Config | `app/services/container/config.py` |
| WarmPool | `app/services/container/warm_pool.py` |
| GC | `app/services/container/gc.py` |
| Credential Proxy | `app/services/proxy/credential_proxy.py` |
| Workspace Agent | `workspace_agent/main.py` |
| Settings | `app/config.py` |

---

## 2. ECS on EC2 移行の根本的な課題

### 2.1 コア課題: Backend↔Workspace間の通信

現行: Backend と Workspace コンテナが**同一ホスト**に存在し、**ホストパスBind Mount経由のUDS**で通信。

ECS: 複数EC2インスタンスにタスクが分散配置される可能性があり、UDS通信は同一ホスト内でしか機能しない。

### 2.2 NetworkMode:none の制約

ECSのNetworkModeはタスクレベルで設定される。`none`を設定すると、タスク内の全コンテナがネットワークインターフェースを持たない。**タスク外部からのTCP/HTTP通信は不可能**。

ただし、同一タスク内のコンテナは**ネットワーク名前空間を共有**し、`localhost`経由で通信可能。

---

## 3. 推奨アーキテクチャ: Single Backend + awsvpc Workspace + Sidecar Proxy

### 3.1 設計思想

ECSのベストプラクティスに従い、**Backend APIは単一のECS Service**として運用し、**Workspaceコンテナは独立してスケール**する構成とする。
- Backend と Workspace は**異なるECSタスク**として分離
- 通信は**VPCプライベートネットワーク経由のHTTP**（UDSから変更）
- Credential Injection Proxy は**Workspace Task内のサイドカーコンテナ**として動作
- ネットワーク隔離は **awsvpc + 制限的Security Group + Route 53 DNS Firewall** で実現

### 3.2 アーキテクチャ概要

```
                    ┌─────────────────────────────────┐
                    │          ALB (HTTPS)             │
                    │       (TLS終端, WAF)             │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │     Backend ECS Service          │
                    │   (awsvpc, 2-4 tasks, Auto Scale)│
                    │                                  │
                    │  ┌──────┐  ┌──────┐  ┌──────┐   │
                    │  │Task 1│  │Task 2│  │Task 3│   │
                    │  │FastAPI│  │FastAPI│ │FastAPI│   │
                    │  └──┬───┘  └──┬───┘  └──┬───┘   │
                    │     │         │         │        │
                    │  SG: backend-sg                  │
                    │  Inbound: ALB SG:8000            │
                    │  Outbound: RDS,Redis,S3,ECR,     │
                    │           workspace-sg:9000      │
                    └──────────────┬──────────────────┘
                                   │ HTTP (VPC内部)
              ┌────────────────────┼────────────────────┐
              │                    │                     │
              ▼                    ▼                     ▼
┌───────────────────────┐ ┌────────────────────┐ ┌────────────────────┐
│  Workspace Task A     │ │  Workspace Task B  │ │  Workspace Task C  │
│  (awsvpc, RunTask)    │ │  (awsvpc, RunTask) │ │  (awsvpc, RunTask) │
│                       │ │                    │ │                    │
│ ┌───────────────────┐ │ │ ┌────────────────┐ │ │ ┌────────────────┐ │
│ │ workspace-agent   │ │ │ │ workspace-agent│ │ │ │ workspace-agent│ │
│ │ (TCP:9000)        │ │ │ │                │ │ │ │                │ │
│ │    ↕ localhost     │ │ │ │    ↕ localhost │ │ │ │    ↕ localhost │ │
│ │ credential-proxy  │ │ │ │ credential-    │ │ │ │ credential-    │ │
│ │ (sidecar, TCP:8080)│ │ │ │ proxy (sidecar)│ │ │ │ proxy (sidecar)│ │
│ └───────────────────┘ │ │ └────────────────┘ │ │ └────────────────┘ │
│                       │ │                    │ │                    │
│ SG: workspace-sg      │ │ SG: workspace-sg  │ │ SG: workspace-sg  │
│ Inbound: backend-sg   │ │ Inbound: backend  │ │ Inbound: backend  │
│          :9000 only   │ │          :9000     │ │          :9000     │
│ Outbound: Bedrock IPs,│ │ Outbound: Bedrock │ │ Outbound: Bedrock │
│          MCP endpoints│ │          MCP       │ │          MCP       │
└───────────────────────┘ └────────────────────┘ └────────────────────┘
```

### 3.3 コンポーネント設計

#### A. Backend Service（ECS Service, awsvpc）
- **配置**: 単一のECS Service（REPLICA strategy, desired count: 2-4）
- **NetworkMode**: `awsvpc`（各タスクが独自のENI/プライベートIPを取得）
- **Auto Scaling**: TargetTrackingPolicy（CPU使用率70%ターゲット）
- **役割**:
  - FastAPI アプリケーション（既存の`app/main.py`）
  - `ecs:RunTask` API で Workspace タスクを起動
  - Redis で conversation_id → workspace_task_ip のマッピングを管理
  - Workspace タスクへの HTTP リクエスト転送
- **Docker Socket**: **不要**（ECS APIを使用）
- **SG（backend-sg）**:
  - Inbound: ALB SG → TCP:8000
  - Outbound: RDS SG:5432, Redis SG:6379, workspace-sg:9000, S3/ECR/CloudWatch VPC Endpoints

#### B. Workspace Task（Standalone Task via RunTask, awsvpc）
- **起動**: Backend から `ecs:RunTask` API呼び出し
- **NetworkMode**: `awsvpc`（独自ENI、制限的SG）
- **内部構造**: 2コンテナ（同一タスク＝同一ネットワーク名前空間 → localhost通信可能）
  1. **workspace-agent**: FastAPI on TCP:9000（UDSから変更）
  2. **credential-proxy**: Proxy on TCP:8080（ホスト側UDSからサイドカーに変更）
- **SG（workspace-sg）**:
  - Inbound: backend-sg → TCP:9000 **のみ**
  - Outbound: Bedrock API IPs:443, MCP endpoint IPs:443 **のみ**（DNS Firewall併用）
  - **全ての不要な通信を暗黙的にDENY**
- **IAM Task Role**: **なし**（認証情報はcredential-proxyサイドカーが管理）

#### C. ALB（Application Load Balancer）
- **ターゲット**: Backend Service の awsvpc タスク群（IP targetType）
- **Sticky Session**: 不要（Backend は stateless、Redis で状態管理）
- **ヘルスチェック**: `/health/live`
- **WAF**: AWS WAF 統合（オプション）

#### D. EC2 Auto Scaling Group + Capacity Provider
- **AMI**: カスタムAMI（ECS最適化AMI + seccompプロファイル）
- **ENI Trunking**: 有効化（`ECS_ENABLE_TASK_ENI_TRUNKING=true`）
- **Capacity Provider**: `managedScaling` 有効、`targetCapacity: 85%`
- **Image Caching**: `ECS_IMAGE_PULL_BEHAVIOR: prefer-cached`
- **Warm Pool**: EC2 Auto Scaling Warm Pool（事前初期化インスタンス）

### 3.4 通信フロー（ECS版）

```
1. Client → ALB → Backend Service (任意のタスク)
2. Backend: Redis検索 → conversation_id → {task_arn, private_ip}?
   ├─ 存在 → HTTP GET http://{workspace_ip}:9000/health でヘルスチェック
   │         → HTTP POST http://{workspace_ip}:9000/execute（SSEストリーム転送）
   └─ なし → ecs:RunTask(workspace-agent-task)
             → DescribeTasks → ENI private IP取得
             → Redis記録 {conversation_id → task_arn, private_ip}
             → HTTP POST http://{workspace_ip}:9000/execute
3. Workspace内通信:
   workspace-agent → http://127.0.0.1:8080 → credential-proxy sidecar
   credential-proxy → Bedrock API (SigV4署名注入)
   credential-proxy → MCP API (認証ヘッダー注入)
4. Response: Backend ← SSEストリーム ← workspace-agent → Client
```

### 3.5 Credential Injection Proxy（サイドカー版）

**重要な変更**: Proxy はBackend側ではなく、**Workspace Task内のサイドカーコンテナ**として動作。

```
Workspace Task (awsvpc, 同一ネットワーク名前空間)
  ┌─────────────────────────────────────────┐
  │                                         │
  │  workspace-agent        credential-proxy│
  │  (TCP:9000)            (TCP:8080)       │
  │       │                     │           │
  │       │ HTTP_PROXY=         │           │
  │       │ http://127.0.0.1:   │           │
  │       │ 8080                │           │
  │       └─────────────────────┘           │
  │              localhost                  │
  │                                         │
  │  ENI (Private IP: 10.0.x.x)            │
  │  SG: workspace-sg                      │
  └─────────────────────────────────────────┘
```

**変更点**:
- credential-proxy が独立コンテナ化（現行: ホスト側 asyncio.start_unix_server）
- 通信: UDS → localhost TCP:8080（socat不要に）
- AWS認証情報: Secrets Manager → credential-proxy に環境変数として注入
- Proxy の Dockerfile 新規作成が必要

**セキュリティ上の考慮**:
- Proxy コンテナに AWS 認証情報を渡す必要がある（Secrets Manager経由）
- workspace-agent からは localhost:8080 経由でのみ外部通信可能
- SG により Proxy の outbound は Bedrock/MCP の IP のみに制限
- **DNS Firewall** により名前解決も許可ドメインのみに制限
- workspace-agent が直接 outbound TCP を試みても SG で DROP される

### 3.6 現行(UDS) vs 提案(awsvpc+HTTP) のセキュリティ比較

| 項目 | 現行 (NetworkMode:none + UDS) | 提案 (awsvpc + SG + DNS Firewall) |
|------|------|------|
| ネットワーク到達性 | 物理的に不可能 | SG + DNS Firewall で制御 |
| 外部DNS解決 | 不可能 | DNS Firewall でドメイン制限 |
| IMDS到達 | 不可能 | awsvpc: `ECS_AWSVPC_BLOCK_IMDS=true` で遮断 |
| ポートスキャン | 不可能（NICなし） | SG Inbound で backend-sg:9000 のみ許可 |
| 認証情報保護 | Proxy がホスト側で完全分離 | Proxy がサイドカー（同一タスク内） |
| Egress制御の強度 | 完全遮断（NICなし） | SG Allow-list + DNS Firewall（多層防御） |

**評価**: ネットワーク隔離の強度は `none` が最強だが、`awsvpc + SG + DNS Firewall` は**十分に実用的なセキュリティレベル**を提供し、ECSベストプラクティスに準拠する。AWSも公式に `awsvpc` を推奨している。

---

## 4. ECS Task Definition 設計

### 4.1 Backend Service Task Definition

```json
{
  "family": "backend-service",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["EC2"],
  "cpu": "2048",
  "memory": "4096",
  "containerDefinitions": [
    {
      "name": "backend",
      "image": "${ECR_REPO}/backend:latest",
      "essential": true,
      "portMappings": [
        { "containerPort": 8000, "protocol": "tcp" }
      ],
      "environment": [
        { "name": "APP_ENV", "value": "production" },
        { "name": "ECS_CLUSTER_NAME", "value": "${CLUSTER_NAME}" },
        { "name": "WORKSPACE_TASK_DEFINITION", "value": "workspace-agent" },
        { "name": "CONTAINER_MANAGER_TYPE", "value": "ecs" },
        { "name": "ECS_WORKSPACE_SUBNETS", "value": "${PRIVATE_SUBNETS}" },
        { "name": "ECS_WORKSPACE_SECURITY_GROUP", "value": "${WORKSPACE_SG_ID}" }
      ],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:...:database-url" },
        { "name": "REDIS_PASSWORD", "valueFrom": "arn:aws:ssm:...:redis-password" },
        { "name": "API_KEYS", "valueFrom": "arn:aws:secretsmanager:...:api-keys" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/backend",
          "awslogs-region": "${AWS_REGION}",
          "awslogs-stream-prefix": "backend"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/health/live || exit 1"],
        "interval": 30,
        "timeout": 10,
        "retries": 3,
        "startPeriod": 15
      }
    }
  ],
  "taskRoleArn": "arn:aws:iam::role/backend-task-role",
  "executionRoleArn": "arn:aws:iam::role/ecs-execution-role"
}
```

**ポイント**:
- `networkMode: awsvpc` — 各タスクが独自ENI（プライベートIP）を取得
- Docker Socket マウント不要（ECS API経由でコンテナ管理）
- Volumes 不要（UDS不使用、全てHTTP通信）

### 4.2 Workspace Task Definition（2コンテナ: agent + proxy sidecar）

```json
{
  "family": "workspace-agent",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["EC2"],
  "containerDefinitions": [
    {
      "name": "workspace-agent",
      "image": "${ECR_REPO}/workspace-base:latest",
      "essential": true,
      "user": "1000:1000",
      "readonlyRootFilesystem": true,
      "privileged": false,
      "portMappings": [
        { "containerPort": 9000, "protocol": "tcp" }
      ],
      "dependsOn": [
        { "containerName": "credential-proxy", "condition": "HEALTHY" }
      ],
      "linuxParameters": {
        "capabilities": {
          "drop": ["ALL"],
          "add": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"]
        },
        "initProcessEnabled": false,
        "tmpfs": [
          { "containerPath": "/tmp", "size": 512, "mountOptions": ["rw", "nosuid"] },
          { "containerPath": "/var/tmp", "size": 256, "mountOptions": ["rw", "noexec", "nosuid"] },
          { "containerPath": "/run", "size": 64, "mountOptions": ["rw", "noexec", "nosuid"] },
          { "containerPath": "/home/appuser/.cache", "size": 512, "mountOptions": ["rw", "noexec", "nosuid"] },
          { "containerPath": "/home/appuser", "size": 128, "mountOptions": ["rw", "noexec", "nosuid"] },
          { "containerPath": "/workspace", "size": 1024, "mountOptions": ["rw", "nosuid"] }
        ]
      },
      "dockerSecurityOptions": ["no-new-privileges:true"],
      "environment": [
        { "name": "CLAUDE_CODE_USE_BEDROCK", "value": "1" },
        { "name": "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "value": "1" },
        { "name": "ANTHROPIC_BEDROCK_BASE_URL", "value": "http://127.0.0.1:8080" },
        { "name": "HTTP_PROXY", "value": "http://127.0.0.1:8080" },
        { "name": "HTTPS_PROXY", "value": "http://127.0.0.1:8080" },
        { "name": "NO_PROXY", "value": "localhost,127.0.0.1" },
        { "name": "HOME", "value": "/home/appuser" },
        { "name": "WORKSPACE_LISTEN_MODE", "value": "tcp" },
        { "name": "WORKSPACE_LISTEN_PORT", "value": "9000" }
      ],
      "cpu": 200,
      "memory": 2048,
      "memoryReservation": 1024,
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/workspace-agent",
          "awslogs-region": "${AWS_REGION}",
          "awslogs-stream-prefix": "agent"
        }
      }
    },
    {
      "name": "credential-proxy",
      "image": "${ECR_REPO}/credential-proxy:latest",
      "essential": true,
      "user": "1000:1000",
      "readonlyRootFilesystem": true,
      "linuxParameters": {
        "capabilities": { "drop": ["ALL"] }
      },
      "dockerSecurityOptions": ["no-new-privileges:true"],
      "portMappings": [],
      "environment": [
        { "name": "PROXY_LISTEN_HOST", "value": "127.0.0.1" },
        { "name": "PROXY_LISTEN_PORT", "value": "8080" },
        { "name": "PROXY_LOG_ALL_REQUESTS", "value": "true" }
      ],
      "secrets": [
        { "name": "AWS_ACCESS_KEY_ID", "valueFrom": "arn:aws:ssm:...:bedrock-access-key" },
        { "name": "AWS_SECRET_ACCESS_KEY", "valueFrom": "arn:aws:ssm:...:bedrock-secret-key" },
        { "name": "AWS_REGION", "valueFrom": "arn:aws:ssm:...:aws-region" },
        { "name": "PROXY_DOMAIN_WHITELIST", "valueFrom": "arn:aws:ssm:...:proxy-whitelist" }
      ],
      "cpu": 128,
      "memory": 256,
      "memoryReservation": 128,
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -sf http://127.0.0.1:8080/health || exit 1"],
        "interval": 15,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 5
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/workspace-proxy",
          "awslogs-region": "${AWS_REGION}",
          "awslogs-stream-prefix": "proxy"
        }
      }
    }
  ],
  "taskRoleArn": null
}
```

**ポイント**:
- `networkMode: awsvpc` — Workspace Task が独自ENIを持ち、Backend から HTTP で到達可能
- `taskRoleArn: null` — IAM Task Role なし（認証情報はcredential-proxyのSecrets経由のみ）
- `dependsOn` — credential-proxy が HEALTHY になるまで workspace-agent の起動を待機
- credential-proxy は `127.0.0.1:8080` でリッスン（外部から直接アクセス不可）
- portMappings はworkspace-agent:9000 のみ（Backend からのアクセス用）

### 4.3 Security Group 設計

```
workspace-sg:
  Inbound:
    - TCP:9000 from backend-sg    # Backend からの /execute リクエストのみ
  Outbound:
    - TCP:443 to bedrock-runtime.{region}.amazonaws.com  (prefix list)
    - TCP:443 to MCP endpoint IPs (必要に応じて追加)
    - TCP:443 to pypi.org, registry.npmjs.org IPs (pip/npm install用)
    - DNS(53) は SG では遮断不可 → Route 53 DNS Firewall で制限

backend-sg:
  Inbound:
    - TCP:8000 from alb-sg        # ALB からのリクエストのみ
  Outbound:
    - TCP:5432 to rds-sg          # PostgreSQL
    - TCP:6379 to redis-sg        # Redis
    - TCP:9000 to workspace-sg    # Workspace タスク
    - TCP:443  to VPC Endpoints   # S3, ECR, CloudWatch, Secrets Manager, ECS API
```

### 4.4 Route 53 DNS Firewall（Egress DNS制限）

SG では VPC 内部 DNS リゾルバ（AmazonProvidedDNS）へのアクセスを遮断できない。
DNS Firewall で Workspace タスクの名前解決を許可ドメインのみに制限する。

```
DNS Firewall Rule Group:
  Priority 1: ALLOW *.amazonaws.com       # Bedrock, S3, ECR
  Priority 2: ALLOW pypi.org              # pip install
  Priority 3: ALLOW files.pythonhosted.org
  Priority 4: ALLOW registry.npmjs.org    # npm install
  Priority 5: ALLOW (MCP endpoints)       # MCP サーバー
  Priority 99: BLOCK *                    # 上記以外は全て拒否

VPC Association: Private Subnet に関連付け
```

### 4.5 seccomp プロファイルの適用

ECS Task Definitionでは `dockerSecurityOptions` に `seccomp=<json>` を**直接指定できない**。

**対策**: カスタムAMIのDocker daemon設定でデフォルトseccompプロファイルを適用:

```json
// /etc/docker/daemon.json on custom AMI
{
  "seccomp-profile": "/etc/docker/seccomp/workspace-seccomp.json",
  "storage-driver": "overlay2",
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

### 4.6 ENI Trunking（インスタンスあたりのタスク数拡大）

awsvpc モードでは各タスクが ENI を消費する。デフォルトでは c6i.2xlarge で最大3 ENI（= 2タスク）のみ。

**ENI Trunking を有効化**することでタスク数を大幅に拡大:

```
# /etc/ecs/ecs.config on custom AMI
ECS_ENABLE_TASK_ENI_TRUNKING=true
ECS_AWSVPC_BLOCK_IMDS=true

# ENI Trunking後の上限例:
# c6i.xlarge:  12 tasks (default: 2)
# c6i.2xlarge: 22 tasks (default: 2)
# c6i.4xlarge: 42 tasks (default: 7)
```

---

## 5. コード変更の設計

### 5.1 変更対象ファイル一覧

| ファイル | 変更内容 | 新規/変更 |
|---------|---------|----------|
| `app/services/container/manager_base.py` | ABC定義 | 新規 |
| `app/services/container/manager_docker.py` | 現行ロジック抽出 | 新規 |
| `app/services/container/manager_ecs.py` | ECS RunTask連携 | 新規 |
| `app/services/container/orchestrator.py` | Managerインターフェース化 + HTTP通信対応 | 変更 |
| `app/services/proxy/credential_proxy.py` | TCP listener対応 + /health追加 | 変更 |
| `app/services/proxy/proxy_container/` | Proxy用Dockerfile + 起動スクリプト | 新規 |
| `app/config.py` | ECS設定項目追加 | 変更 |
| `workspace_agent/main.py` | UDS/TCP切り替え対応 | 変更 |
| `workspace-base/entrypoint.sh` | socat条件分岐 (TCP時は不要) | 変更 |
| `workspace-base/Dockerfile.workspace` | ECS用最適化 | 変更 |

### 5.2 Container Manager の抽象化（Strategy Pattern）

```
app/services/container/
  ├── manager_base.py          # ABC: ContainerManagerBase
  ├── manager_docker.py        # 現行: aiodocker直接 (ローカル開発用)
  ├── manager_ecs.py           # 新規: ECS RunTask API (本番用)
  ├── orchestrator.py          # 変更: Manager切り替え + HTTP通信
  ├── lifecycle.py             # 変更なし（Docker用は残す）
  ├── config.py                # 変更: ECS用設定追加
  └── warm_pool.py             # 変更: ECS版ウォームプール対応
```

#### `manager_base.py` (新規)
```python
from abc import ABC, abstractmethod
from app.services.container.models import ContainerInfo

class ContainerManagerBase(ABC):
    @abstractmethod
    async def create_container(self, container_id: str) -> ContainerInfo: ...
    @abstractmethod
    async def destroy_container(self, container_id: str, grace_period: int) -> None: ...
    @abstractmethod
    async def is_healthy(self, container_id: str) -> bool: ...
    @abstractmethod
    async def get_container_endpoint(self, container_id: str) -> str:
        """コンテナの通信エンドポイントを返す。
        Docker: UDSパス (e.g. /var/run/workspace-sockets/{id}/agent.sock)
        ECS: HTTP URL (e.g. http://10.0.1.50:9000)
        """
        ...
    @abstractmethod
    async def list_workspace_containers(self) -> list[dict]: ...
```

#### `manager_ecs.py` (新規) の主要メソッド
```python
import aioboto3

class ECSContainerManager(ContainerManagerBase):
    """ECS RunTask APIを使ったコンテナ管理"""

    async def create_container(self, container_id: str) -> ContainerInfo:
        # 1. ecs:RunTask (awsvpc, workspace-sg, private subnets)
        # 2. ecs:DescribeTasks → ENI attachment → private IP取得
        # 3. wait_for_agent_ready(http://{ip}:9000/health)
        # 4. ContainerInfo返却（task_arn=ID, endpoint=http://{ip}:9000）
        response = await self.ecs.run_task(
            cluster=self.cluster_name,
            taskDefinition=self.workspace_task_def,
            count=1,
            launchType='EC2',
            networkConfiguration={
                'awsvpcConfiguration': {
                    'subnets': self.subnets,
                    'securityGroups': [self.workspace_sg],
                    'assignPublicIp': 'DISABLED'
                }
            },
            overrides={
                'containerOverrides': [{
                    'name': 'credential-proxy',
                    'environment': [
                        # RunTask時にMCPヘッダールールを注入
                        {'name': 'MCP_HEADER_RULES', 'value': json.dumps(mcp_rules)}
                    ]
                }]
            }
        )

    async def destroy_container(self, container_id: str, grace_period: int):
        await self.ecs.stop_task(cluster=self.cluster_name, task=container_id)

    async def is_healthy(self, container_id: str) -> bool:
        # ecs:DescribeTasks でステータス確認 + HTTP /health チェック
        tasks = await self.ecs.describe_tasks(cluster=self.cluster_name, tasks=[container_id])
        task = tasks['tasks'][0]
        if task['lastStatus'] != 'RUNNING':
            return False
        # HTTP health check
        ip = self._get_task_ip(task)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://{ip}:9000/health")
            return resp.status_code == 200

    async def get_container_endpoint(self, container_id: str) -> str:
        # Redis cache: task_arn → private_ip
        ip = await self.redis.hget(f"workspace:task:{container_id}", "private_ip")
        return f"http://{ip}:9000"
```

### 5.3 Orchestrator 変更 (`orchestrator.py`)

主な変更点:
1. `ContainerLifecycleManager` → `ContainerManagerBase` に差し替え
2. UDS transport → HTTP transport に切り替え（ECS時）
3. Proxy起動を Orchestrator から削除（ECS時はサイドカーが自動起動）
4. MCP ヘッダールールを RunTask overrides で注入

```python
# Before (UDS)
transport = httpx.AsyncHTTPTransport(uds=agent_socket)
async with httpx.AsyncClient(transport=transport) as client:
    async with client.stream("POST", "http://localhost/execute", ...):

# After (ECS: HTTP over VPC)
endpoint = await self.manager.get_container_endpoint(info.id)
async with httpx.AsyncClient(timeout=...) as client:
    async with client.stream("POST", f"{endpoint}/execute", ...):
```

### 5.4 workspace_agent/main.py 変更

UDS/TCP の切り替え対応:
```python
LISTEN_MODE = os.environ.get("WORKSPACE_LISTEN_MODE", "uds")  # "uds" or "tcp"
LISTEN_PORT = int(os.environ.get("WORKSPACE_LISTEN_PORT", "9000"))
AGENT_SOCKET = "/var/run/ws/agent.sock"

if __name__ == "__main__":
    if LISTEN_MODE == "tcp":
        logger.info("ワークスペースエージェント起動 (TCP)", port=LISTEN_PORT)
        uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT, log_level="info")
    else:
        logger.info("ワークスペースエージェント起動 (UDS)", socket=AGENT_SOCKET)
        uvicorn.run(app, uds=AGENT_SOCKET, log_level="info")
```

### 5.5 credential-proxy コンテナ化

Proxy を独立コンテナとして切り出す。

```
app/services/proxy/
  ├── credential_proxy.py       # 既存: TCP/UDS両対応に変更
  ├── proxy_container/
  │   ├── Dockerfile            # 新規: Proxy用軽量イメージ
  │   ├── main.py               # 新規: エントリポイント（TCP listener起動）
  │   └── requirements.txt      # httpx, structlog のみ
  ├── sigv4.py                  # 既存: 変更なし
  └── domain_whitelist.py       # 既存: 変更なし
```

Proxy の Dockerfile:
```dockerfile
FROM python:3.11-slim
RUN pip install --no-cache-dir httpx structlog
COPY app/services/proxy/ /opt/proxy/
USER 1000:1000
HEALTHCHECK --interval=15s --timeout=5s CMD curl -sf http://127.0.0.1:8080/health || exit 1
ENTRYPOINT ["python", "-m", "proxy.main"]
```

### 5.6 設定ファイル変更 (`app/config.py`)

```python
# ============================================
# ECS設定
# ============================================
container_manager_type: str = "docker"  # "docker" or "ecs"
ecs_cluster_name: str = ""
ecs_workspace_task_definition: str = "workspace-agent"
ecs_workspace_subnets: str = ""      # カンマ区切り
ecs_workspace_security_group: str = ""
```

### 5.7 MCP ヘッダールールの受け渡し方式変更

現行: Orchestrator が Proxy.update_mcp_header_rules() を直接呼び出し（同一プロセス内）

ECS版: Proxy はサイドカーで別コンテナ。2つの方式が考えられる:

**方式A: RunTask overrides で環境変数として渡す**
- メリット: シンプル、追加通信不要
- デメリット: 会話中にルールが変わった場合、タスク再起動が必要

**方式B: Backend → Proxy HTTP API で動的更新**
- Proxy に `/admin/mcp-rules` POST エンドポイントを追加
- Backend が HTTP で Proxy に直接ルール送信（workspace-agent の port 9000 とは別ポート or 同じENI）
- メリット: 動的更新可能
- デメリット: SG で追加ポート許可が必要（or workspace-agent 経由でルーティング）

**推奨: 方式A**（現行ではMCPルールは execute リクエスト毎に設定されるが、1会話中のルールは基本固定のため）

---

## 6. インフラストラクチャ構成

### 6.1 AWS リソース一覧

```
VPC
├── Public Subnet x2 (AZ-a, AZ-c) — ALB配置
├── Private Subnet x2 (AZ-a, AZ-c) — ECS タスク、RDS、Redis配置
├── NAT Gateway x2 (各AZに1つ) — Workspace Proxy → 外部通信用
│
├── ALB
│   ├── Target Group (Backend Service, port 8000, IP target type)
│   ├── Listener: HTTPS:443 → TG
│   └── Health Check: /health/live
│
├── ECS Cluster
│   ├── Capacity Provider (EC2 ASG)
│   │   ├── Auto Scaling Group
│   │   │   ├── Launch Template (Custom AMI)
│   │   │   ├── Instance Type: c6i.2xlarge (8vCPU, 16GB) — 推奨
│   │   │   ├── Min: 2, Max: 10, Desired: 2
│   │   │   └── Warm Pool: Min 1 (Stopped state)
│   │   └── managedScaling: enabled, targetCapacity: 85%
│   │
│   ├── ECS Service: backend-service (REPLICA, desired: 2-4)
│   │   ├── Task Definition: backend-service
│   │   ├── NetworkMode: awsvpc
│   │   ├── Service Auto Scaling: Target Tracking (CPU 70%)
│   │   └── Load Balancer: ALB Target Group
│   │
│   └── Standalone Tasks: workspace-agent (RunTask API)
│       ├── Task Definition: workspace-agent (2 containers)
│       └── NetworkMode: awsvpc
│
├── ECR
│   ├── backend:latest
│   ├── workspace-base:latest
│   └── credential-proxy:latest  ← 新規
│
├── RDS PostgreSQL (or Aurora Serverless v2)
│   └── Private Subnet, Multi-AZ
│
├── ElastiCache Redis (Cluster Mode Disabled, Multi-AZ)
│   └── Private Subnet
│
├── S3 (既存)
│   └── Workspace ファイル保存
│
├── Secrets Manager / SSM Parameter Store
│   ├── database-url (Secrets Manager)
│   ├── api-keys (Secrets Manager)
│   ├── bedrock-access-key (SSM SecureString)
│   ├── bedrock-secret-key (SSM SecureString)
│   ├── redis-password (SSM SecureString)
│   └── proxy-whitelist (SSM String)
│
├── Route 53 DNS Firewall
│   ├── Rule Group: workspace-dns-rules
│   └── VPC Association: Private Subnet
│
├── VPC Endpoints (Gateway / Interface)
│   ├── S3 (Gateway)
│   ├── ECR (Interface: ecr.api, ecr.dkr)
│   ├── CloudWatch Logs (Interface)
│   ├── Secrets Manager (Interface)
│   ├── SSM (Interface)
│   └── ECS (Interface: ecs, ecs-agent, ecs-telemetry)
│
├── CloudWatch
│   ├── Log Groups: /ecs/backend, /ecs/workspace-agent, /ecs/workspace-proxy
│   ├── Alarms: CPU, Memory, Task Count, RunTask Failures
│   └── Dashboard: コンテナ状態、レイテンシ、エラー率
│
└── IAM Roles
    ├── backend-task-role:
    │   ├── ecs:RunTask, ecs:StopTask, ecs:DescribeTasks
    │   ├── s3:GetObject, s3:PutObject, s3:ListBucket
    │   ├── iam:PassRole (execution role のみ)
    │   └── Condition: ecs:cluster = ${CLUSTER_ARN}
    ├── ecs-execution-role:
    │   ├── ecr:GetAuthorizationToken, ecr:BatchGetImage
    │   ├── logs:CreateLogStream, logs:PutLogEvents
    │   ├── ssm:GetParameters (proxy secrets)
    │   └── secretsmanager:GetSecretValue (backend secrets)
    └── ec2-instance-role:
        ├── ecs:RegisterContainerInstance
        ├── ecs:DeregisterContainerInstance
        ├── ecr:GetAuthorizationToken
        └── logs:CreateLogStream
```

### 6.2 カスタムAMI構成

```
Base: Amazon ECS-Optimized Amazon Linux 2023 AMI
  + /etc/docker/daemon.json:
      seccomp-profile: /etc/docker/seccomp/workspace-seccomp.json
      storage-driver: overlay2
  + /etc/docker/seccomp/workspace-seccomp.json
  + /etc/ecs/ecs.config:
      ECS_CLUSTER=${CLUSTER_NAME}
      ECS_IMAGE_PULL_BEHAVIOR=prefer-cached
      ECS_ENABLE_TASK_ENI_TRUNKING=true
      ECS_AWSVPC_BLOCK_IMDS=true
      ECS_WARM_POOLS_CHECK=true
      ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION=1h
      ECS_CONTAINER_STOP_TIMEOUT=30s
  + iptables rule: IMDS block for containers (defense in depth)
  + Packer ビルドで CI/CD パイプラインに統合
```

### 6.3 インスタンスタイプ推奨（ENI Trunking有効時）

| 用途 | インスタンスタイプ | ENI Trunk枠 | 同時タスク目安 |
|-----|------------------|------------|-------------|
| ステージング | c6i.xlarge (4vCPU, 8GB) | ~12 | Backend 1 + Workspace 8-10 |
| 本番(標準) | c6i.2xlarge (8vCPU, 16GB) | ~22 | Backend 1 + Workspace 15-18 |
| 本番(大規模) | c6i.4xlarge (16vCPU, 32GB) | ~42 | Backend 1 + Workspace 30-35 |

計算: Workspace 1タスク = CPU 0.2core + MEM 2GB（agent） + CPU 0.128core + MEM 0.256GB（proxy）
→ c6i.2xlarge (8vCPU, 16GB) で CPU的には最大24タスク、MEM的には最大7タスク。
→ **MEMがボトルネック**: ワークスペース約7タスク/インスタンス × ASG 2-10台 = **14-70 同時ワークスペース**

---

## 7. ローカル開発戦略

### 7.1 方針: docker-compose を維持しつつ ECS互換パスも用意

ローカル開発では**現行のdocker-compose構成を維持**。`CONTAINER_MANAGER_TYPE=docker` でaiodockerベースのマネージャーを使用。

```yaml
# docker-compose.yml (変更最小限)
services:
  backend:
    environment:
      CONTAINER_MANAGER_TYPE: docker  # ローカルはDocker直接
      # UDS通信パスを維持
      WORKSPACE_SOCKET_BASE_PATH: /var/run/workspace-sockets
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/run/workspace-sockets:/var/run/workspace-sockets
```

ローカルでは現行と同じ UDS + NetworkMode:none パターンを使用。ECS環境では `CONTAINER_MANAGER_TYPE=ecs` に切り替えるだけで awsvpc + HTTP パターンに切り替わる。

### 7.2 環境切り替え

| 環境 | CONTAINER_MANAGER_TYPE | 通信方式 | Proxy起動 | インフラ |
|------|----------------------|---------|----------|---------|
| ローカル開発 | `docker` | UDS | Orchestrator内 | docker-compose |
| ステージング | `ecs` | HTTP (VPC) | サイドカー | ECS on EC2 |
| 本番 | `ecs` | HTTP (VPC) | サイドカー | ECS on EC2 (Multi-AZ) |

### 7.3 テスト戦略

| テスト種別 | 環境 | 検証対象 |
|-----------|------|---------|
| Unit test | ローカル | Manager ABC, Orchestrator ロジック |
| Integration test | ローカル (docker) | UDS通信、Proxy、コンテナライフサイクル |
| Integration test | ステージング | ECS RunTask、HTTP通信、SG、DNS Firewall |
| E2E test | ステージング | 全体フロー（ALB→Backend→Workspace→Bedrock）|
| Security test | ステージング | ネットワーク隔離、IMDS遮断、Credential漏洩 |
| Load test | ステージング/本番 | 同時コンテナ数、スケーリング、レイテンシ |

---

## 8. ウォームプール / 高速起動戦略

### 8.1 EC2 レベル Warm Pool

```
EC2 Auto Scaling Group
├── Active Instances: Min 2
├── Warm Pool: Min 1 (Stopped state, reuse)
│   └── ECS Agent: ECS_WARM_POOLS_CHECK=true
└── Cold Start: 4+ min → Warm Start: ~36s
```

### 8.2 コンテナレベル高速起動

| 最適化 | 手法 | 効果 |
|--------|------|------|
| イメージキャッシュ | `ECS_IMAGE_PULL_BEHAVIOR: prefer-cached` | 初回以降のpull不要 |
| イメージサイズ | workspace-base + credential-proxy 軽量化 | pull時間短縮 |
| Bin-packing | binpack配置戦略 | キャッシュヒット率向上 |
| ENI Trunking | 有効化 | ENIプリプロビジョニング |

### 8.3 アプリケーションレベル Warm Pool

現行のRedisベースWarmPoolは**ECS版でも維持**:
- Backend Service 起動時に `warm_pool_min_size` 分のRunTaskを事前発行
- 起動済みタスクの `{task_arn, private_ip}` をRedis Listに格納
- acquire() → LPOP → HTTP /health チェック → 返却
- replenish() バックグラウンドタスクで不足分をRunTaskで補充

**RunTask起動時間の見積もり**（EC2、キャッシュ済みイメージ、awsvpc + ENI Trunking）:
- ECS Scheduler + ENIプロビジョニング: 3-8秒
- コンテナ起動 (proxy + agent): 2-4秒
- credential-proxy HEALTHY: 2-3秒
- workspace_agent Ready: 2-5秒
- **合計: 約9-20秒**（現行のDocker直接: 2-5秒と比較して遅延）
- **Warm Pool使用時: 即時**（pre-startedタスクを割り当てるだけ）

---

## 9. セキュリティ比較

### 9.1 現行 vs ECS on EC2

| セキュリティ項目 | 現行 (Docker直接) | ECS on EC2 (提案) | 評価 |
|----------------|-------------------|-------------------|------|
| ネットワーク隔離 | NetworkMode:none | awsvpc + restrictive SG + DNS Firewall | ⚠️ SG/DNSで制御（noneほどではない） |
| Egress制御 | 物理的に不可 | SG allow-list + DNS Firewall | ⚠️ 多層防御だが物理遮断ではない |
| Capability制限 | CapDrop:ALL + 4 Add | linuxParameters.capabilities | ✅ 同等 |
| ReadonlyRootfs | ✅ | readonlyRootFilesystem: true | ✅ 同等 |
| tmpfs | HostConfig.Tmpfs (uid/gid指定) | linuxParameters.tmpfs (size+options) | ⚠️ uid/gid指定不可（entrypointで対応） |
| seccomp | SecurityOpt直接指定 | Docker daemon設定で適用 | ⚠️ 間接的だが機能は同等 |
| AppArmor | SecurityOpt | dockerSecurityOptions | ✅ 同等 |
| no-new-privileges | SecurityOpt | dockerSecurityOptions | ✅ 同等 |
| User | 1000:1000 | user: "1000:1000" | ✅ 同等 |
| 認証情報保護 | Host側Proxy（完全分離） | サイドカー（同一タスク内） | ⚠️ タスク内で localhost到達可能 |
| IMDS遮断 | N/A（ネットワークなし） | ECS_AWSVPC_BLOCK_IMDS=true | ✅ ECS設定で遮断 |
| タスク間隔離 | N/A (単一ホスト) | ❌ EC2ではタスク間隔離なし | ⚠️ リスク（緩和策あり） |

### 9.2 ECS on EC2 固有のセキュリティリスクと緩和策

#### リスク1: タスク間クレデンシャル漏洩 (2025年7月 Sweet Security報告)
同一EC2上のタスクが他タスクのIAMクレデンシャルにアクセスできる可能性。
- **緩和策**: Workspace TaskにIAM Task Roleを**割り当てない**（`taskRoleArn: null`）
- **効果**: Workspace タスクにはIAMクレデンシャルが存在しないため、漏洩するものがない
- **Bedrock認証情報**: credential-proxy サイドカーの Secrets Manager 経由のみ

#### リスク2: IMDS攻撃 (2025年10月 Latacora報告)
- **緩和策1**: `ECS_AWSVPC_BLOCK_IMDS=true`（ECS Agent設定）
- **緩和策2**: Launch TemplateでIMDSv2強制 + hop limit 1
- **緩和策3**: iptables によるコンテナからの 169.254.169.254 ブロック（AMI設定）

#### リスク3: SG egress bypass（DNS経由のデータ漏洩）
SG では DNS をブロックできないため、DNSトンネリングの可能性。
- **緩和策**: Route 53 DNS Firewall で許可ドメイン以外のDNS解決をブロック
- **効果**: 未許可ドメインへのDNSクエリ自体が失敗する

#### リスク4: credential-proxy サイドカーのセキュリティ
サイドカーは workspace-agent と同じタスク（同じネットワーク名前空間）内。
- **緩和策**: proxy は 127.0.0.1:8080 でリッスン（SG の inbound:9000 とは別）
- **緩和策**: proxy の secrets は ECS execution role 経由で注入（workspace-agent からは不可視）
- **注意**: 同一タスク内のコンテナは互いの環境変数にアクセスできない（ECSの設計）

### 9.3 tmpfs uid/gid 問題の対策

ECSのtmpfs設定では `uid` / `gid` mount optionを直接指定できない。

**対策**: Workspace Dockerfileの `ENTRYPOINT` で `chown` を実行（CapAdd: CHOWN があるため可能）

---

## 10. スケーラビリティ分析

### 10.1 現行の限界 vs 提案

| 指標 | 現行 | ECS on EC2 (提案) |
|------|------|-------------------|
| 同時コンテナ数 | 50-100 (単一ホスト) | 14-70+ (ASG 2-10台) |
| Backend | 1プロセス | 2-4タスク (Auto Scale) |
| 障害影響範囲 | 全コンテナ喪失 | 1インスタンス分のみ |
| スケール速度 | 手動 | 自動 (Capacity Provider) |
| リソース効率 | 固定サーバー | bin-packing + 自動縮退 |
| ホスト依存 | 完全依存 | なし（Workspace は任意のインスタンスに配置） |

### 10.2 スケーリングフロー

```
Backend Service Auto Scaling (CPU 70% target)
    ↓ (Backend自体も独立スケール)

Workspace RunTask → "タスク配置不可" (リソース or ENI不足)
    ↓
Capacity Provider → CapacityProviderReservation > targetCapacity (85%)
    ↓
ASG Scale-Out → 新EC2起動 (Warm Poolから優先)
    ↓
ECS Agent登録 → ENI Trunk 準備完了
    ↓
Workspace RunTask 再試行 → 成功
```

### 10.3 ボトルネックと対策

| ボトルネック | 対策 |
|-------------|------|
| ENI枠不足 | ENI Trunking有効化 + 大きめインスタンスタイプ |
| RunTask起動遅延 (9-20秒) | アプリWarm Pool（pre-started tasks in Redis） |
| RunTask APIレート制限 | バースト時のリトライ + 指数バックオフ |
| EC2起動遅延 (4+ min) | EC2 Warm Pool + prefer-cached |
| Redisシングルポイント | ElastiCache Multi-AZ + 自動フェイルオーバー |
| PostgreSQLボトルネック | RDS Multi-AZ or Aurora Serverless v2 |

---

## 11. 代替アーキテクチャの検討

### 11.1 案B: DAEMON Backend + NetworkMode:none + UDS パターン

```
各EC2インスタンスに Backend DAEMON を配置
  └─ Host Path Bind Mount で UDS通信を維持
  └─ Workspace Task は NetworkMode:none
```

**メリット**: NetworkMode:none の最強ネットワーク隔離を維持
**デメリット**:
- Backend が各インスタンスに複数存在（管理複雑）
- ALBスティッキーセッション必須（会話がインスタンスに紐づく）
- Backend のスケーリングが EC2 インスタンス数に固定される
- AWS推奨の awsvpc パターンから逸脱

**評価**: セキュリティ最優先で、スケーラビリティ要件が低い場合に検討

### 11.2 案C: ECS Managed Instances（2025年9月GA）

**概要**: AWSが全自動でEC2を管理する新しいCapacity Provider。Bottlerocket OS ベース。

**現状の制約**:
- Docker daemon設定のカスタマイズ不可（seccompプロファイル適用不可）
- カスタムAMI非対応（AWSが OS/エージェントを管理）
- 14日間隔で自動パッチ（ドレイン→入替）

**将来的な選択肢**: Managed Instances がseccompカスタマイズをサポートした場合、最も運用コストの低い選択肢。Spotインスタンスも2025年12月から対応しておりコスト面でも有利。

### 11.3 案D: Fargate

**メリット**: タスク間の **microVM隔離**（最強の隔離レベル）、インフラ管理不要
**デメリット**:
- NetworkMode は `awsvpc` のみ（`none` 非対応）
- seccompカスタムプロファイル不可
- イメージキャッシュなし（毎回pull）→ 起動が遅い
- EC2より高コスト

**評価**: セキュリティ要件がタスク間隔離を求める場合（マルチテナントで信頼できないコード実行など）に検討。microVM隔離はEC2にはない強み。

### 11.4 案E: ECS on EC2 + Fargate ハイブリッド

```
Backend Service → Fargate (管理不要、軽量)
Workspace Tasks → EC2 (seccomp, カスタムAMI, コスト最適化)
```

**メリット**: Backend管理の簡素化 + Workspace のカスタマイズ性
**デメリット**: 2つのCapacity Providerの管理が必要
**評価**: 検討に値する構成。Backend は Fargate でも問題なく動作するため、運用負荷を軽減可能。

---

## 12. 移行実装ステップ

### Phase 1: コード抽象化（ローカル開発維持、本番影響なし）
1. `ContainerManagerBase` ABC作成
2. 現行ロジックを `DockerContainerManager` に抽出
3. `Orchestrator` を `ContainerManagerBase` に依存するよう変更
4. `CONTAINER_MANAGER_TYPE` 設定追加（default: "docker"）
5. workspace_agent/main.py に TCP listen mode 追加
6. テスト: 既存テストが全てパスすることを確認

### Phase 2: credential-proxy コンテナ化
1. credential_proxy.py をTCP listener対応に変更
2. `/health` エンドポイント追加
3. Proxy用 Dockerfile 作成
4. MCP ヘッダールールの環境変数注入対応
5. docker-compose でのローカルテスト（sidecar構成）

### Phase 3: ECS Container Manager 実装
1. `ECSContainerManager` 実装（aioboto3 ECS client）
2. RunTask / StopTask / DescribeTasks の統合
3. ENI private IP取得ロジック
4. WarmPool の ECS対応（RunTaskベース pre-start）
5. GC の ECS対応（StopTaskベース TTL管理）
6. ローカルでの単体テスト（moto mock）

### Phase 4: IaC 構築
1. Terraform モジュール構成:
   ```
   terraform/
     ├── modules/
     │   ├── vpc/          # VPC, Subnets, NAT GW, VPC Endpoints
     │   ├── ecs/          # Cluster, Capacity Provider, ASG, AMI
     │   ├── services/     # Backend Service, Task Definitions
     │   ├── database/     # RDS, ElastiCache
     │   ├── security/     # SGs, IAM Roles, DNS Firewall
     │   └── monitoring/   # CloudWatch, Alarms, Dashboard
     ├── environments/
     │   ├── staging/
     │   └── production/
     └── packer/           # Custom AMI build
   ```
2. カスタムAMI構築（Packer + GitHub Actions）
3. Secrets Manager / SSM パラメータ初期設定

### Phase 5: CI/CD パイプライン
1. GitHub Actions workflow:
   - lint + test → ECR build & push (3 images) → Terraform plan
   - Manual approval → Terraform apply → ECS Service update
2. ECRライフサイクルポリシー（古いイメージ自動削除）
3. Packer AMI ビルドパイプライン（月次 or セキュリティパッチ時）

### Phase 6: ステージング環境デプロイ & 検証
1. Terraform apply (staging)
2. 機能テスト: Backend→Workspace→Bedrock 全体フロー
3. セキュリティテスト:
   - Workspace タスクから IMDS アクセス試行 → ブロック確認
   - Workspace タスクから未許可ドメインへの接続試行 → SG/DNS Firewall でブロック確認
   - workspace-agent から credential-proxy の secrets アクセス試行 → 不可確認
4. 負荷テスト: 同時20-50ワークスペース起動、ASGスケーリング動作
5. 障害テスト: EC2 terminate、タスク異常終了、Redis failover

### Phase 7: 本番移行
1. Terraform apply (production)
2. DNS切り替え（Blue/Green or カナリア）
3. 監視・アラート閾値調整
4. 運用手順書・ランブック作成

---

## 13. リスク評価と緩和策

| リスク | 影響度 | 発生確率 | 緩和策 |
|--------|--------|---------|--------|
| RunTask起動遅延（9-20秒） | 中 | 高 | アプリWarm Pool (pre-started tasks) |
| NetworkMode:none→awsvpc移行 | 中 | 中 | SG + DNS Firewall + IMDS Block の多層防御 |
| seccomp適用の間接性 | 低 | 中 | カスタムAMI + Packer CI/CD |
| タスク間クレデンシャル漏洩 | 高 | 低 | taskRoleArn: null + Secrets Manager経由 |
| ENI枠不足 | 中 | 中 | ENI Trunking + 十分なインスタンスサイズ |
| tmpfs uid/gid制限 | 低 | 高 | entrypoint.sh で chown 実行 |
| ECS API eventually consistent | 低 | 中 | DescribeTasks のexponential backoff |
| EC2 Warm Pool枯渇 | 中 | 低 | Cold start fallback + アラート |
| コスト増（managed infra） | 中 | 高 | Reserved Instances + 使用量に応じたASGスケール |
| MCP ルール動的更新 | 低 | 低 | 環境変数方式（1会話中は固定） |

---

## 14. コスト概算比較

### 現行（単一EC2）
- c6i.2xlarge (On-Demand): ~$0.34/h ≈ $245/月
- 合計: ~$300/月（EC2 + ストレージ）

### ECS on EC2（ステージング）
- EC2 (c6i.xlarge x2): ~$0.17 x 2 = $0.34/h ≈ $245/月
- ALB: ~$25/月
- RDS (db.t3.medium): ~$50/月
- ElastiCache (cache.t3.micro): ~$15/月
- NAT Gateway: ~$45/月
- CloudWatch Logs: ~$10/月
- 合計: ~$390/月

### ECS on EC2（本番）
- EC2 (c6i.2xlarge x2-10, Reserved): ~$180-900/月
- ALB: ~$30/月
- RDS (db.r6g.large, Multi-AZ): ~$300/月
- ElastiCache (cache.r6g.large): ~$200/月
- NAT Gateway x2: ~$90/月
- CloudWatch: ~$30/月
- Secrets Manager: ~$5/月
- ECR: ~$10/月
- 合計: ~$845-1,565/月

---

## 15. 結論

### 実現可能性: ✅ 高い

**推奨構成: Single Backend Service + awsvpc Workspace Tasks + Sidecar Proxy**

**根拠**:
1. **単一Backend**: ECS Service (REPLICA) として独立スケール。Workspace と完全に分離。
2. **Workspace独立スケール**: RunTask API で任意のインスタンスに動的配置。Backend のスケールに依存しない。
3. **セキュリティ多層防御**: awsvpc + restrictive SG + DNS Firewall + IMDS Block で、NetworkMode:none に匹敵する実用的なセキュリティレベルを達成。
4. **ECSベストプラクティス準拠**: AWS公式推奨の awsvpc モード、サイドカーパターン、Capacity Provider を採用。
5. **ローカル開発互換**: `CONTAINER_MANAGER_TYPE` の切り替えで、docker-compose 環境をそのまま維持。

### 主要トレードオフ
| | 現行 | ECS提案 |
|--|------|---------|
| **スケール** | 50-100コンテナ (単一ホスト) | 14-70+ (ASG自動スケール) |
| **障害耐性** | 単一障害点 | Multi-AZ、1インスタンス障害のみ影響 |
| **ネットワーク隔離** | NetworkMode:none (最強) | awsvpc + SG + DNS Firewall (十分) |
| **起動遅延** | 2-5秒 | 9-20秒 (Warm Poolで即時) |
| **コスト** | ~$300/月 | ~$850-1,500/月 |
| **運用負荷** | Docker直接管理 | ECS + Terraform 管理 |

### 推奨事項
1. **Phase 1-2（抽象化 + Proxy コンテナ化）を最優先**: コード変更量が少なく、ローカル開発への影響なし
2. **ステージング環境で徹底的にセキュリティ検証**: SG Egress、DNS Firewall、IMDS遮断
3. **IaCはTerraformを推奨**: ECSモジュールの成熟度が高い
4. **将来的にBackend Fargate化 or ECS Managed Instances移行を視野に入れる**

---

## 参考情報源

### AWS 公式ドキュメント
- [ECS Task Networking Options](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking.html)
- [ECS Task Definition Parameters (EC2)](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters_ec2.html)
- [ECS Network Security Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-network.html)
- [ECS Task & Container Security Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-tasks-containers.html)
- [Optimize ECS Task Launch Time](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-recommendations.html)
- [Bind Mounts with ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html)
- [ECS Capacity Providers for EC2](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html)
- [Speeding up ECS Container Deployments (Nathan Peck)](https://nathanpeck.com/speeding-up-amazon-ecs-container-deployments/)
- [RunTask API Reference](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html)
- [VPC Security Groups (Egress Rules)](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html)
- [Restricting Outbound Traffic (AWS Prescriptive Guidance)](https://docs.aws.amazon.com/prescriptive-guidance/latest/secure-outbound-network-traffic/restricting-outbound-traffic.html)

### AWS 新機能 (2025-2026)
- [ECS Managed Instances Deep Dive](https://aws.amazon.com/blogs/containers/deep-dive-amazon-ecs-managed-instances-provisioning-and-optimization/)
- [ECS Managed Instances Announcement (Sep 2025)](https://aws.amazon.com/about-aws/whats-new/2025/09/amazon-ecs-managed-instances/)
- [ECS Managed Instances Spot Support (Dec 2025)](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-ecs-managed-instances-ec2-spot-instances/)
- [ECS Managed Instances in All Regions (Oct 2025)](https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-ecs-managed-instances-commercial-regions/)
- [AWS CDK 2026 ECS Improvements](https://www.techedubyte.com/aws-cdk-2026-mixins-eks-bedrock-ecs-updates/)

### セキュリティリサーチ (2025)
- [ECS on EC2: IMDS Hardening Gaps (Latacora, Oct 2025)](https://www.latacora.com/blog/2025/10/02/ecs-on-ec2-covering-gaps-in-imds-hardening/)
- [Cross-Task Credential Exposure (Sweet Security, Jul 2025)](https://www.sweet.security/blog/under-the-hood-of-amazon-ecs-on-ec2-agents-iam-roles-and-task-isolation)
- [ECS Security Patterns (Medium)](https://medium.com/@amit2067/amazon-ecs-security-patterns-and-practices-3841deaaaea4)
- [Hardening ECS (Medium)](https://medium.com/@ataouadoumhissein36/hardening-amazon-ecs-d98b0b99245a)
- [Docker Seccomp Profiles](https://docs.docker.com/engine/security/seccomp/)

### アーキテクチャパターン
- [Using Ephemeral Environments to Sandbox Agentic Workflows (Shipyard)](https://shipyard.build/blog/sandboxing-agentic-workflows/)
- [ECS Sidecar Share Data Between Containers (Medium)](https://medium.com/@dipandergoyal/aws-ecs-sidecar-share-data-between-containers-16a992480cb)
- [NGINX Reverse Proxy Sidecar for ECS](https://containersonaws.com/pattern/nginx-reverse-proxy-sidecar-ecs-fargate-task/)
- [EKS vs ECS vs Fargate Comparison 2025](https://www.clustox.com/blog/aws-container-comparison/)
