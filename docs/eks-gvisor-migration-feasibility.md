# EKS + gVisor 移行構成 — 実現可能性調査レポート

## Context

現行のDockerコンテナ直接管理方式（aiodocker → Docker API）を、EKS + gVisor + Karpenter（Spot）構成に移行する計画の実現可能性を調査する。特に以下の3点を明確にする：

1. **ローカル開発環境で動作するか**
2. **アーキテクチャ上の問題点・懸念事項**
3. **実装可能性と変更範囲の正確な評価**

---

## 1. ローカル環境での動作可否

### 結論: gVisorはローカルで動作しない。デュアルモード構成が必須

| プラットフォーム | gVisor対応 | 理由 |
|:---:|:---:|:---|
| **Linux** | △ 条件付き | gVisor(runsc)はLinuxカーネル4.15+で動作。ただしminikube/k3s上での手動セットアップが必要 |
| **macOS** | ✕ 不可 | gVisorはDarwinカーネル非対応。Docker Desktop内のLinux VMでも公式サポートなし |
| **Windows** | ✕ 不可 | WSL2内でのgVisor動作は非公式・不安定 |

### ローカルK8sツールのgVisor対応状況

| ツール | gVisor | 備考 |
|:---:|:---:|:---|
| minikube | △ | Linux限定。`--container-runtime=containerd`でrunsc手動インストール要 |
| kind | ✕ | Docker-in-Docker構成のためrunscシム設定不可 |
| k3s | △ | Linux限定。containerdランタイムハンドラ手動設定要 |
| Docker Desktop K8s | ✕ | DockerランタイムのためOCIハンドラ非対応 |
| microk8s | ○ | Linux限定。snap経由でカスタムOCIランタイム対応 |

### 推奨: デュアルモードアーキテクチャ

```
ContainerBackend (Protocol/ABC)
  ├── DockerBackend   ← ローカル開発（現行コードをそのまま利用）
  └── K8sBackend      ← 本番EKS（新規実装）
```

- 環境変数 `CONTAINER_BACKEND=docker|kubernetes` で切り替え
- macOS/Windows開発者は現行のDocker方式を継続使用
- Linux開発者はオプションでk3s+gVisorをローカルテスト可能
- CI/CDではEKSステージング環境でK8sパスをテスト

### デュアルモードの制約

- **セキュリティの差異**: ローカル(seccomp+AppArmor+NetworkMode:none) vs 本番(gVisor+NetworkPolicy)は異なるサンドボックス技術。ローカルで検出できないセキュリティバグが存在しうる
- **通信プロトコルの差異**: ローカル(Unix Domain Socket) vs 本番(TCP over Pod Network)でHTTPキープアライブ等の挙動が異なる
- **ボリュームの差異**: Docker tmpfs vs K8s emptyDir（後述）

---

## 2. アーキテクチャ上の問題点・懸念事項

### 2.1 ネットワーク隔離の大幅な劣化（最重要懸念）

**現行**: `NetworkMode: "none"` — コンテナにネットワークインターフェースが存在しない。全外部通信はsocat→UDS→バックエンドProxyを経由。これは極めて強固な隔離。

**移行後**: NetworkPolicyに依存 — Pod内のサイドカーProxyが外部通信を行う必要があるため、**Pod自体にegressが必要**。

- NetworkPolicyはCNIプラグイン（Calico, Cilium）依存。AWS VPC CNI単体ではNetworkPolicyを強制しない
- Pod間通信がデフォルトで可能（default-denyポリシーの明示設定が必須）
- gVisorサンドボックスからの脱出が成功した場合、Proxyのドメインホワイトリストをバイパスされる可能性
- DNSへのアクセスが情報漏洩経路になりうる

**対策案**:
- `sandboxes` namespaceにdefault-deny egressポリシーを適用
- gVisorのnetstackによるネットワーク制限の活用を検討
- Pod Security Standards (Restricted) を適用

### 2.2 workspace_agent の変更が必要（「変更なし」は不正確）

`workspace_agent/main.py:140`:
```python
uvicorn.run(app, uds=AGENT_SOCKET, log_level="info")
```

K8s環境ではPod IP:8080でTCPリスンに変更が必要:
```python
uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
```

さらに `workspace-base/entrypoint.sh:29` のsocat起動も、サイドカーProxy構成では不要になる。

→ **workspace_agentとentrypoint.shは変更対象に含めるべき**

### 2.3 Credential Injection Proxyのサイドカー化

**現行** (`app/services/proxy/credential_proxy.py`): バックエンドプロセス内で動作し、UDSでリスン。AWS認証情報はバックエンドのみが保持。

**サイドカー化の影響**:

| 項目 | 影響 |
|:---|:---|
| **AWS認証情報** | 各Podが認証情報にアクセスする必要あり。IRSAが推奨（Pod ServiceAccount → IAM Role） |
| **MCPヘッダールール注入** | 現行は`orchestrator.py`から直接Pythonメソッド呼び出し（`update_mcp_header_rules`）。サイドカーでは管理API（`/rules`エンドポイント）を新設し、HTTPで注入する設計が必要 |
| **ライフサイクル管理** | 現行の`_start_proxy`/`_stop_proxy`/`_restart_proxy`はK8s側ではkubeletが管理。細かい制御は不可になる |
| **ドメインホワイトリスト** | 環境変数またはConfigMapで注入 |

### 2.4 コールドスタートの大幅な遅延

| シナリオ | Docker (現行) | K8s + gVisor (移行後) |
|:---|:---|:---|
| WarmPool Hit | ~200ms | ~500ms (K8s API overhead) |
| コールドスタート | ~2-5s | **30-120s** (ノードプロビジョニング、イメージPull、gVisor初期化) |
| exec 1回 | ~50ms | ~200-500ms |
| ファイル同期(10ファイル) | ~500ms | ~2-5s |

→ **WarmPoolのmin_sizeを5以上に増加し、Karpenterのover-provisioningを設定する必要がある**

### 2.5 emptyDir vs tmpfs

- 現行: `Tmpfs`（メモリバック、uid/gid/mode指定）→ `config.py:109-118`
- 移行後: `emptyDir`はデフォルトでディスクバック。メモリバックにするには `medium: Memory` を指定するがPodのメモリ制限にカウントされOOMリスクあり
- 提案の5Giは現行1Gから大幅増。Spotインスタンスのディスク容量に注意
- 権限設定: `securityContext.runAsUser: 1000` で対応可能

### 2.6 Spot Instanceプリエンプション

- 2分前の警告でPodが強制終了。実行中のワークスペース処理が失われる
- 現行の `orchestrator.py` のクラッシュリカバリロジック（L191-204, L219-229）が自然にSpotプリエンプションをConnectionErrorとして処理可能
- ただしS3同期完了前の中断でデータ損失の可能性

---

## 3. コード変更範囲の詳細分析

### 変更が必要なファイル一覧

#### 大幅な書き換え / 新規作成

| ファイル | 変更内容 |
|:---|:---|
| `app/services/container/lifecycle.py` | K8s版LifecycleManager新規実装。全メソッド(create, destroy, is_healthy, exec, list)をK8s API対応に |
| `app/services/container/config.py` | K8s Pod spec生成関数を追加（RuntimeClass, sidecar, emptyDir, SecurityContext, labels） |
| `app/services/proxy/credential_proxy.py` | サイドカー用コンテナイメージ化 + 管理API(`/rules`)エンドポイント追加 |
| **新規** `app/services/container/backend.py` | `ContainerLifecycleBackend` Protocol/ABC定義 |
| **新規** `app/services/container/k8s_lifecycle.py` | `K8sLifecycleManager` 実装 |
| **新規** K8sマニフェスト群 | RuntimeClass, NetworkPolicy, Karpenter Provisioner, IRSA |

#### 中程度の変更

| ファイル | 変更内容 |
|:---|:---|
| `app/services/container/models.py` | `pod_name`, `pod_ip`, `namespace`フィールド追加。Redis hash更新 |
| `app/services/container/orchestrator.py` | UDS→TCP transport切替、Proxy管理をサイドカー対応に、MCPルール注入をHTTP化 |
| `app/services/container/warm_pool.py` | lifecycle抽象レイヤー経由に変更（ロジック自体はほぼ同一） |
| `app/services/container/gc.py` | Docker API応答パース → K8s Pod応答パースに変更 |
| `app/config.py` | K8s設定追加: `container_backend`, `k8s_namespace`, `k8s_runtime_class`, `proxy_sidecar_image`等 |
| `app/core/lifespan.py` | バックエンド選択ファクトリ（Docker or K8s） |
| `workspace_agent/main.py` | UDS/TCP デュアルモード対応（環境変数で切替） |
| `workspace-base/entrypoint.sh` | サイドカー構成時のsocat削除/分岐 |

#### 変更不要（抽象レイヤーが適切に実装された場合）

| ファイル | 理由 |
|:---|:---|
| `app/services/execute_service.py` | orchestrator経由で間接的に利用。抽象化で透過的 |
| `app/services/workspace/s3_storage.py` | コンテナランタイム非依存 |
| `app/services/workspace/file_sync.py` | lifecycle.exec_in_container()経由。抽象化で透過的 |
| フロントエンド全体 | APIインターフェース変更なし |
| 全APIエンドポイント | orchestrator APIが同一であれば変更不要 |
| DBモデル・マイグレーション | コンテナランタイム非依存 |

### 新規依存関係

```
# requirements.txt に追加
kubernetes>=31.0.0   # K8s Python client
```

`aiodocker` はDockerモード用に残す。

---

## 4. 実装可能性の総合判定

### 実装可能: Yes（ただし以下の条件付き）

| 判定項目 | 結果 | 備考 |
|:---|:---:|:---|
| 技術的実現性 | ○ | 全変更は標準的なK8s APIとPython clientで実装可能 |
| ローカル開発互換性 | △ | gVisor不可。デュアルモード必須 |
| セキュリティ維持 | △ | NetworkMode:none → NetworkPolicyは劣化。gVisorがユーザー空間カーネルで補完 |
| パフォーマンス | △ | コールドスタート10-60倍遅延。WarmPool拡大で対策 |
| 運用複雑性 | ✕ | Docker Compose → EKS+Karpenter+gVisor+IRSA+NetworkPolicyは大幅増 |
| コスト | ○ | Spot活用で40-50%削減見込み（EKS control plane $73/月を差し引いても） |
| 変更スコープ | △ | 「container/配下のみ」は概ね正しいが、workspace_agent, proxy, config, lifespanも変更対象 |

### 主要リスク

1. **セキュリティ**: NetworkPolicy誤設定によるPod間通信漏洩
2. **パフォーマンス**: Spotノードプロビジョニング遅延（最大120秒）
3. **gVisor互換性**: Node.js native module / Python C extensionの一部がgVisorで動作しない可能性（要事前テスト）
4. **運用負荷**: EKSクラスタ管理、カスタムAMIビルド、gVisorアップデート

---

## 5. 推奨実装戦略

### フェーズ分割（インクリメンタル移行を強く推奨）

**Phase 1: 抽象化レイヤー構築**
- `ContainerLifecycleBackend` Protocol定義
- 現行Dockerコードを`DockerLifecycleManager`にリファクタ
- 既存テスト全パス確認

**Phase 2: K8sバックエンド実装**
- `K8sLifecycleManager` 実装
- Pod spec生成（config.py）
- K8s exec ラッパー
- workspace_agent TCP対応
- モックK8s APIでのユニットテスト

**Phase 3: Proxyサイドカー化**
- credential_proxy.pyのコンテナイメージ化
- `/rules` 管理APIエンドポイント追加
- orchestratorからHTTPでルール注入

**Phase 4: インフラ構築（Phase 2-3と並行可）**
- EKSクラスタ（Terraform/CDK）
- Karpenter Provisioner設定
- gVisor入りカスタムAMI
- NetworkPolicy定義
- IRSA設定

**Phase 5: 統合テスト**
- ステージングEKSで全E2Eテスト
- Spotプリエンプションテスト
- パフォーマンスベンチマーク
- セキュリティ監査

**Phase 6: 本番移行**
- Blue-Green デプロイ
- 段階的トラフィック移行（10% → 50% → 100%）
- ロールバック: `CONTAINER_BACKEND=docker` に切り替え

---

## 6. 検証方法

### ローカル（デュアルモード検証）
```bash
# Dockerモード（現行動作の確認）
CONTAINER_BACKEND=docker docker-compose up backend
# 既存E2Eテスト実行
pytest tests/e2e/ -v
```

### K8sモード（Linux環境 or CI）
```bash
# k3s + gVisor セットアップスクリプト実行
./scripts/setup-local-k8s.sh
# K8sバックエンドでのE2Eテスト
CONTAINER_BACKEND=kubernetes pytest tests/e2e/ -v
```

### ステージングEKS
- Karpenter Spotノードプロビジョニング確認
- gVisor RuntimeClassでのPod起動確認
- NetworkPolicy疎通テスト（許可/拒否の両方）
- 3,000同時Pod負荷テスト
- Spotプリエンプション時のリカバリテスト
