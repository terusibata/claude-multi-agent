# ECS on EC2 移行調査

## 結論: 実現可能。推奨構成あり。

**現行の問題**: aiodocker単一ホスト → 同時50-100コンテナが限界、ホスト障害=全喪失

**推奨構成**: Backend(ECS Service) + Workspace(RunTask) を分離し、独立スケール

---

## 1. 現行 → ECS: 何が変わるか

```
【現行】                              【ECS提案】
単一EC2ホスト                          複数EC2 + ALB
  Backend (Docker API直接)               Backend ECS Service (2-4タスク, 自動スケール)
    ↓ UDS (Unix Socket)                    ↓ HTTP (VPCプライベート)
  Workspace (NetworkMode:none)           Workspace Task (awsvpc + 制限SG)
    ↕ UDS via socat                        ↕ localhost (同一タスク内)
  Credential Proxy (Host側プロセス)      Credential Proxy (サイドカーコンテナ)
```

### 3つの重要な設計変更

| 変更点 | 現行 | ECS版 | 理由 |
|--------|------|-------|------|
| **通信** | UDS (Unix Socket) | HTTP (VPC内) | マルチホストではUDS不可 |
| **ネットワーク** | NetworkMode:none | awsvpc + SG + DNS Firewall | Backendから到達するためENI必要 |
| **Proxy** | Backend内プロセス | Workspace Task内サイドカー | Backendと同一ホストの保証がないため |

---

## 2. アーキテクチャ図

```
             ALB (HTTPS)
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌────────┐   Backend ECS Service
│ FastAPI │ │ FastAPI │ │ FastAPI │   (REPLICA, 自動スケール)
│ Task 1  │ │ Task 2  │ │ Task 3  │   SG: ALB→8000のみ
└───┬─────┘ └───┬─────┘ └────────┘
    │ HTTP       │ HTTP
    ▼            ▼
┌──────────────┐ ┌──────────────┐    Workspace Standalone Tasks
│ workspace-   │ │ workspace-   │    (RunTask API, 会話ごとに1つ)
│ agent :9000  │ │ agent :9000  │
│   ↕localhost  │ │   ↕localhost  │    同一タスク内=同一NW名前空間
│ credential-  │ │ credential-  │    → localhostで通信可能
│ proxy :8080  │ │ proxy :8080  │
│ (sidecar)    │ │ (sidecar)    │    SG: Backend→9000のみIN
└──────────────┘ └──────────────┘        Bedrock/MCP→443のみOUT
```

**ポイント**:
- Backend は stateless な ECS Service。Redis で会話→Workspace IPを管理
- Workspace は `ecs:RunTask` で動的起動。各タスクが独自のプライベートIP
- credential-proxy がサイドカーとして同じタスク内で動作 → socat不要、localhost通信

---

## 3. セキュリティ: 現行 vs ECS

| 項目 | 現行 | ECS | 判定 |
|------|------|-----|------|
| ネットワーク隔離 | none (NICなし) | awsvpc + SG + DNS Firewall | ⚠️ 物理遮断→論理遮断 |
| Egress制御 | 完全遮断 | SG allowlist + DNS Firewall | ⚠️ 多層防御で補完 |
| CapDrop/Rootfs/seccomp | 全て適用 | 全て適用可能 | ✅ 同等 |
| 認証情報保護 | Host側Proxy | サイドカー(Secrets Manager経由) | ✅ 実質同等 |
| IMDS | 到達不可 | `BLOCK_IMDS=true` で遮断 | ✅ 対応可能 |
| タスク間隔離 | N/A | EC2上では隔離なし | ⚠️ TaskRole=null で緩和 |

**総評**: NetworkMode:none の「物理的遮断」は失うが、SG + DNS Firewall + IMDS Block の多層防御で**実用上十分なセキュリティ**を確保。AWS公式もawsvpc推奨。

### 主要リスクと対策（3つ）

1. **タスク間クレデンシャル漏洩** → Workspace に IAM Role を割り当てない (`taskRoleArn: null`)
2. **IMDS攻撃** → `ECS_AWSVPC_BLOCK_IMDS=true` + IMDSv2強制 + iptables
3. **DNS経由データ漏洩** → Route 53 DNS Firewall で許可ドメイン以外をブロック

---

## 4. コード変更（Strategy Pattern で切り替え）

環境変数 `CONTAINER_MANAGER_TYPE` で Docker/ECS を切り替える。

```
CONTAINER_MANAGER_TYPE=docker  → ローカル開発（現行のまま）
CONTAINER_MANAGER_TYPE=ecs     → ステージング/本番（ECS RunTask）
```

### 変更ファイル

| ファイル | 内容 |
|---------|------|
| `manager_base.py` **(新規)** | ABC: create/destroy/is_healthy/get_endpoint |
| `manager_docker.py` **(新規)** | 現行 aiodocker ロジック抽出 |
| `manager_ecs.py` **(新規)** | RunTask/StopTask/DescribeTasks |
| `orchestrator.py` **(変更)** | Manager切替 + UDS→HTTP切替 |
| `credential_proxy.py` **(変更)** | TCP listener対応 + /health追加 |
| `proxy_container/Dockerfile` **(新規)** | Proxy独立コンテナ |
| `workspace_agent/main.py` **(変更)** | UDS/TCP切替 |
| `config.py` **(変更)** | ECS設定追加 |

### Orchestrator の変更（最小限）
```python
# Before (Docker, UDS)
transport = httpx.AsyncHTTPTransport(uds=agent_socket)
client.stream("POST", "http://localhost/execute", ...)

# After (ECS, HTTP)
endpoint = await self.manager.get_container_endpoint(info.id)  # http://10.0.x.x:9000
client.stream("POST", f"{endpoint}/execute", ...)
```

---

## 5. インフラ構成

```
VPC
├── Public Subnet x2 ─── ALB
├── Private Subnet x2 ── ECS Tasks, RDS, Redis
├── NAT Gateway x2
├── ECS Cluster
│   ├── Capacity Provider (EC2 ASG, managedScaling, Warm Pool)
│   ├── Service: backend (awsvpc, REPLICA, Auto Scale)
│   └── Tasks: workspace (awsvpc, RunTask, 動的起動)
├── RDS PostgreSQL (Multi-AZ)
├── ElastiCache Redis (Multi-AZ)
├── ECR (backend / workspace-base / credential-proxy)
├── Route 53 DNS Firewall (Egress DNS制限)
├── VPC Endpoints (S3, ECR, CloudWatch, SecretsManager, ECS)
└── カスタムAMI (seccomp profile + ENI Trunking + IMDS Block)
```

### ENI Trunking（重要）

awsvpc では各タスクがENIを消費。デフォルトだとインスタンスあたり2-3タスクしか動かない。
**ENI Trunking 有効化**で大幅に拡大:

| インスタンス | ENI Trunk後のタスク数 | メモリ制約による実効値 |
|------------|---------------------|---------------------|
| c6i.xlarge (4vCPU, 8GB) | 12 | ~3ワークスペース |
| c6i.2xlarge (8vCPU, 16GB) | 22 | ~7ワークスペース |
| c6i.4xlarge (16vCPU, 32GB) | 42 | ~14ワークスペース |

---

## 6. 起動速度とWarm Pool

| | 現行 (Docker) | ECS (コールド) | ECS (Warm Pool) |
|--|--------------|---------------|----------------|
| 起動時間 | 2-5秒 | 9-20秒 | 即時 |

**対策**: 現行のRedis Warm Poolをそのまま移植。Backend起動時にRunTaskで事前起動 → Redis にストック → acquire()で即座に割当。

---

## 7. 環境切り替え

| 環境 | Manager | 通信 | Proxy | インフラ |
|------|---------|------|-------|---------|
| ローカル | docker | UDS | Backend内 | docker-compose |
| ステージング | ecs | HTTP | サイドカー | ECS on EC2 |
| 本番 | ecs | HTTP | サイドカー | ECS on EC2 (Multi-AZ) |

ローカル開発は**docker-compose のまま変更なし**。

---

## 8. コスト比較

| | 現行 | ステージング | 本番 |
|--|------|------------|------|
| 月額 | ~$300 | ~$390 | ~$850-1,500 |
| 内訳 | EC2 1台 | EC2 x2 + ALB + RDS + Redis + NAT | EC2 x2-10 + 同左(Multi-AZ) |

---

## 9. 代替案を棄却した理由

| 案 | 概要 | 棄却理由 |
|----|------|---------|
| DAEMON + none + UDS | 各EC2にBackend配置、UDS維持 | Backendが増殖、スケール不自由 |
| ECS Managed Instances | AWS全自動管理のEC2 | seccompカスタム不可、AMIカスタム不可 |
| Fargate | serverless、microVM隔離 | none非対応、seccomp不可、キャッシュなし |
| Fargate + EC2 ハイブリッド | BackendはFargate | 将来的には良い。まずEC2で統一が簡潔 |

---

## 10. 移行ステップ（7 Phase）

| Phase | 内容 | 影響範囲 |
|-------|------|---------|
| **1** | ContainerManagerBase ABC作成 + Docker Manager抽出 | コードのみ、動作変更なし |
| **2** | credential-proxy コンテナ化 (TCP対応, Dockerfile) | 新コンテナ追加 |
| **3** | ECS Container Manager 実装 (RunTask/StopTask) | 新モジュール |
| **4** | Terraform IaC構築 (VPC/ECS/RDS/Redis/SG/IAM) | インフラ |
| **5** | CI/CD パイプライン (GitHub Actions → ECR → ECS) | 自動化 |
| **6** | ステージング検証 (機能/セキュリティ/負荷/障害) | テスト |
| **7** | 本番移行 (Blue/Green) | リリース |

**Phase 1-2 を最優先**: ローカル開発に影響なく、後続のECS対応基盤になる。

---

## 11. リスクまとめ

| リスク | 対策 |
|--------|------|
| 起動遅延 (9-20秒) | Warm Pool (pre-started tasks) |
| NetworkMode:none喪失 | SG + DNS Firewall + IMDS Block |
| seccomp間接適用 | カスタムAMI (Docker daemon設定) |
| タスク間隔離なし | taskRoleArn: null (漏洩するものがない) |
| ENI枠不足 | ENI Trunking有効化 |
| コスト増 ($300→$850+) | Reserved Instances + ASG自動縮退 |

---

## 参考情報源

**AWS公式**: [Task Networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking.html) / [Security Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-tasks-containers.html) / [Task Launch Optimization](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-recommendations.html) / [Capacity Providers](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html) / [RunTask API](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html)

**2025-2026新機能**: [ECS Managed Instances](https://aws.amazon.com/blogs/containers/deep-dive-amazon-ecs-managed-instances-provisioning-and-optimization/) / [Managed Instances + Spot](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-ecs-managed-instances-ec2-spot-instances/) / [CDK 2026 ECS](https://www.techedubyte.com/aws-cdk-2026-mixins-eks-bedrock-ecs-updates/)

**セキュリティ**: [IMDS Hardening Gaps (Latacora 2025)](https://www.latacora.com/blog/2025/10/02/ecs-on-ec2-covering-gaps-in-imds-hardening/) / [Cross-Task Credential Exposure (Sweet Security 2025)](https://www.sweet.security/blog/under-the-hood-of-amazon-ecs-on-ec2-agents-iam-roles-and-task-isolation) / [Docker Seccomp](https://docs.docker.com/engine/security/seccomp/)
