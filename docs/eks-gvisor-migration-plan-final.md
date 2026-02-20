# EKS+gVisor移行 最終プラン (AI向け圧縮版)

## TL;DR
Docker直接管理(単一ホスト,50-100user上限)→EKS+gVisor+Karpenter(3000+user)移行。デュアルモード(Docker/K8s)切替で既存開発フロー維持。

---

## 現行構成の限界
- aiodocker→Docker API直接管理。単一ホスト設計
- UDS通信(socat経由)、NetworkMode:none(NIC無し)で強固な隔離
- スケール上限: CPU/MEM物理制約で同時50-100container
- 単一障害点: ホスト障害=全container喪失

## スタック選定

### K8s Client: kr8s(一般操作)+kubernetes_asyncio(exec streaming)
- kr8s: async-first,httpxベース(既存依存一致),kubectl風API
- kubernetes_asyncio: WebSocket exec必須(kr8sはexec非対応)
- lightkube: exec非対応で不可。公式kubernetes-client: 同期設計で不可

### IaC: Terraform+EKS Blueprints
### マニフェスト: Kustomize(base+overlays:local/staging/prod)
### CI/CD: GitHub Actions(IaC)+ArgoCD(K8s GitOps)

### ローカル開発
- kind(upstream K8s,v1.32ピン) + Tilt(live_update,1-3s反映)
- mirrord(ステージングEKSプロキシ) + LocalStack(S3/SQS模擬)
- gVisorはmacOS/Windows不可→ローカルはDockerモード維持

### 本番AWS
- EKS K8s1.32, Karpenter v1, AL2023+gVisor AMI
- EKS Pod Identity(IRSA後継,OIDC不要)
- VPC CNI native NetworkPolicy(eBPF,追加daemon不要)
- KEP-753 Native Sidecar(initContainers+restartPolicy:Always)
- Aurora PostgreSQL Serverless v2, ElastiCache Redis cluster
- Amazon Managed Prometheus+Grafana

---

## アーキテクチャ

### デュアルモード抽象化

```
app/services/container/
├── backend.py      # NEW: ContainerBackend Protocol
├── lifecycle.py    # KEEP(5file import): DockerBackend,Protocol準拠修正
├── k8s_backend.py  # NEW: KubernetesBackend(kr8s+kubernetes_asyncio)
├── config.py       # MOD: get_pod_manifest()追加
├── models.py       # MOD: pod_ip,pod_name,agent_port追加
├── orchestrator.py # MOD: UDS→TCP切替,Proxy管理変更
├── warm_pool.py    # MOD: 型ヒントのみ(Protocol化)
└── gc.py           # MOD: K8s Pod応答パース
```

lifecycle.pyリネーム不可: lifespan.py,file_sync.py,gc.py,orchestrator.py,warm_pool.pyがimport

### Protocol定義
```python
class ContainerBackend(Protocol):
    async def create_container(self,conversation_id:str="")->ContainerInfo
    async def destroy_container(self,container_id:str,grace_period:int=30)->None
    async def is_healthy(self,container_id:str,check_agent:bool=False)->bool
    async def exec_in_container(self,container_id:str,cmd:list[str])->tuple[int,str]
    async def exec_in_container_binary(self,container_id:str,cmd:list[str])->tuple[int,bytes]
    async def list_workspace_containers(self)->list[dict]
    async def wait_for_agent_ready(self,agent_address:str,timeout:float=30.0,container_id:str|None=None)->bool
    async def get_container_logs(self,container_id:str,tail:int=50)->str
```
get_container_logs: orchestrator.py:332のDocker API直接アクセス抽象化

切替: CONTAINER_BACKEND=docker|kubernetes
WarmPoolManager/ContainerGarbageCollector: バックエンド非依存のまま

### クラスタトポロジ

```
EKS Cluster(K8s1.32)
├── backend ns
│   └── FastAPI Deployment(2+replicas) ← ALB Ingress
│       NodePool: backend-ondemand(On-Demand,m/c family,large-xlarge)
│       理由: ステートフル接続管理,Spot中断不可
├── sandboxes ns
│   └── Workspace Pod(per conversation) ← RuntimeClass:gvisor
│       [sidecar] credential-proxy(:8080,HTTP_PROXY)
│       [main] workspace-agent(:8081,/execute,/health)
│       NodePool: workspace-spot(Spot,m/c/r family,9+types)
│       emptyDir:/workspace(5Gi), /tmp(Memory,512Mi)
│       NetworkPolicy: default-deny-egress + allow-proxy-443 + allow-dns
├── karpenter ns (Fargate)
└── monitoring ns
```

### NodePool分離設計

**backend-ondemand NodePool:**
- capacity-type: on-demand (Spot不可:WebSocket接続管理中)
- instance-family: m6i,m7i,c6i,c7i (large,xlarge)
- replicas: 2+ (HPA: CPU70%target)
- disruption: WhenEmpty,consolidateAfter:10m

**workspace-spot NodePool:**
- capacity-type: spot (中断許容:ステートレス短命Pod)
- instance-family: m5,m6i,m7i,c5,c6i,c7i,r5,r6i,r7i (large-2xlarge)
- 15+ instance types(Spot容量確保)
- expireAfter:336h, consolidation:WhenEmptyOrUnderutilized,2m
- EC2NodeClass: AL2023+gVisor AMI,gp3 50Gi,httpPutResponseHopLimit:2

### Workspace Pod Spec要点
- runtimeClassName:gvisor, activeDeadlineSeconds:14400(4h)
- sidecar: KEP-753(initContainers+restartPolicy:Always)
  - credential-proxy: cpu100m-500m,mem128Mi-256Mi,port8080
  - ALLOWED_DOMAINS=bedrock-runtime.{region}.amazonaws.com
- main: workspace-agent: cpu500m-2000m,mem512Mi-2Gi,port8081
  - HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:8080
  - AGENT_LISTEN_MODE=tcp, AGENT_PORT=8081
  - readinessProbe: /health:8081,initial3s,period5s
- securityContext: runAsUser1000,runAsNonRoot,readOnlyRootFilesystem
  - capabilities: drop ALL, add CHOWN/SETUID/SETGID/DAC_OVERRIDE

### NetworkPolicy
- default-deny-egress(sandboxes ns全Pod)
- ingress: backend ns→:8081のみ許可
- egress: :443(Bedrock API)+:53/UDP(kube-dns)のみ許可
- 注: NetworkPolicyはPod単位(container単位不可)。sidecar egress許可=main containerも理論上アクセス可。gVisorカーネルレベル制御で多層防御。将来Cilium L7検討可

### コールドスタート対策(3層)

**L1: Redis WarmPool(現行ロジック維持)**
min_size:10,max_size:50(本番拡大)

**L2: Pause Pod Overprovisioning**
PriorityClass value:-1, replicas:5, resources=workspace同等
→実Pod作成時にpreempt,ノード待ちなし

**L3: Image Pre-pull DaemonSet**
initContainersでworkspace/proxy image pull→全node cache

**期待レイテンシ:**
- WarmPool Hit: ~500ms
- WarmPool Miss(node空き): ~5-10s
- 完全コールド: ~30-60s

---

## レイテンシ分析(現行vs移行後)

| 操作 | Docker現行 | K8s+gVisor |
|---|---|---|
| WarmPool Hit | ~200ms | ~500ms(K8s API overhead) |
| コールドスタート | ~2-5s | 30-60s(対策後) |
| exec 1回 | ~50ms(UDS) | ~100-200ms(TCP+gVisor syscall overhead) |
| file_sync 10files | ~500ms | ~1-2s |
| AI応答(Bedrock) | 2-15s | 2-15s(変化なし,支配的) |

ユーザー体感: AI応答時間(2-15s)が支配的→exec overhead増(+50-150ms)は体感差なし

---

## コスト見積(3000user,同時300Pod想定)

### 月額概算
| 項目 | 月額USD | 備考 |
|---|---|---|
| EKS Control Plane | $73 | 固定 |
| Workspace Nodes(Spot) | $800-1500 | m6i.xlarge Spot~$0.06/h,平均15-25node |
| Backend Nodes(On-Demand) | $200-400 | m6i.large x2-4 |
| Aurora Serverless v2 | $200-500 | 0.5-4ACU |
| ElastiCache Redis | $100-200 | cache.r6g.large |
| ALB | $30-50 | |
| データ転送 | $50-100 | |
| Prometheus+Grafana | $50-100 | |
| **合計** | **$1500-2900/月** | |

現行Docker単一ホスト(c5.4xlarge等): ~$500/月だが50-100user上限
→ per-user単価: 現行$5-10 vs 移行後$0.5-1.0 (大幅改善)

---

## 変更ファイル一覧

| File | Type | Detail |
|---|---|---|
| container/backend.py | NEW | ContainerBackend Protocol |
| container/k8s_backend.py | NEW | KubernetesBackend(kr8s+kubernetes_asyncio) |
| container/lifecycle.py | MOD | Protocol準拠+get_container_logs()。リネーム不可 |
| container/config.py | MOD | get_pod_manifest() |
| container/models.py | MOD | pod_ip,pod_name,agent_port |
| container/orchestrator.py | MOD | UDS→TCP,Proxy管理 |
| container/gc.py | MOD | K8s Pod応答パース |
| container/warm_pool.py | MOD | 型ヒントのみ |
| proxy/credential_proxy.py | MOD | TCP対応+/rules API(sidecar用) |
| app/config.py | MOD | container_backend,K8s設定 |
| core/lifespan.py | MOD | backend選択factory |
| workspace_agent/main.py | MOD | AGENT_LISTEN_MODE=uds|tcp |
| workspace-base/entrypoint.sh | MOD | tcp時socat省略 |
| infra/terraform/ | NEW | EKS,Karpenter,VPC,IAM |
| k8s/base/ | NEW | Kustomize base |
| k8s/overlays/{local,staging,prod}/ | NEW | env overlays |

**変更不要(確認済):**
- execute_service.py: orchestrator経由で透過
- workspace/: backend非依存
- API endpoints: orchestrator API同一
- Frontend: API変更なし
- DB models: runtime非依存

### 依存追加
```
kr8s>=0.20.15
kubernetes-asyncio>=31.1.0
```
aiodockerはDockerモード用に残す

---

## 実装Phase

### P1: 抽象化レイヤー
- ContainerBackend Protocol(backend.py)
- lifecycle.py Protocol準拠修正(リネーム不可)
- orchestrator.py:332 Docker API直接→get_container_logs()抽象化
- orchestrator.py Protocol経由化
- warm_pool.py型ヒント更新
- 既存テスト全パス確認

### P2: K8sバックエンド
- k8s_backend.py(kr8s+kubernetes_asyncio)
- config.py Pod spec生成
- models.py K8sフィールド
- workspace_agent TCP listen
- entrypoint.sh socat分岐
- kind単体テスト

### P3: Proxyサイドカー化
- credential_proxy.py TCP+/rules API
- Proxy Dockerfile
- Native Sidecar Pod spec
- orchestrator.py sidecar対応

### P4: インフラ(P2-3並行可)
- Terraform: VPC,EKS,Karpenter IAM,Pod Identity
- Packer: AL2023+gVisor AMI
- Kustomize: base+overlays
- Tiltfile

### P5: 統合テスト
- staging EKS E2E
- gVisor互換性(Node.js,Python C ext)
- Spot preemptionリカバリ
- NetworkPolicy疎通
- 3000同時Pod負荷

### P6: 本番移行
- Blue-Green deploy
- 段階的移行: 10%→50%→100%
- Rollback: CONTAINER_BACKEND=docker即時切替

---

## リスクと対策

| Risk | Impact | Mitigation |
|---|---|---|
| NetworkPolicy誤設定 | Pod間通信漏洩 | default-deny+allow-list,gVisor多層防御 |
| Spotプリエンプション | 実行中処理喪失 | 既存crashリカバリ(orchestrator.py L191-229)で自然対処 |
| gVisor非互換 | Node.js native module/Python C ext動作不可 | P5で事前テスト,fallback:runc RuntimeClass |
| コールドスタート遅延 | 最大30-60s | 3層対策(WarmPool+PausePod+ImageCache) |
| NetworkPolicyがPod単位 | sidecar egress=main containerもアクセス可 | gVisorカーネル制御+将来Cilium L7 |

## 検証コマンド

```bash
# Local Docker mode
CONTAINER_BACKEND=docker pytest tests/ -v

# Local K8s mode(kind)
kind create cluster --image=kindest/node:v1.32.2 --name test
kubectl apply -k k8s/overlays/local/
CONTAINER_BACKEND=kubernetes pytest tests/ -v

# Staging EKS
kubectl get nodes -l karpenter.sh/capacity-type=spot
kubectl run test --image=busybox --runtime-class=gvisor -- sleep 30
kubectl exec test -- dmesg | grep -i gvisor
kubectl exec -n sandboxes ws-test -- curl -m5 http://example.com  # timeout
kubectl exec -n sandboxes ws-test -- curl -m5 http://backend.backend:8000/health  # ok
```
