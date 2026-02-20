# マルチテナント・ワークスペース スケーリング調査

> **調査日**: 2026年2月20日
> **前提**: 収益性を確保しつつ、数百〜数千の同時接続ワークスペースをサポートする構成を選定

---

## 1. 収益性シミュレーション

### 想定価格帯（ワークスペース1時間あたり）

| 利用者向け課金 | 月額換算(8h/日×22日) | 位置づけ |
|--------------|---------------------|---------|
| ¥50/hr | ¥8,800/月 | 個人向け低価格 |
| ¥100/hr | ¥17,600/月 | 標準的なSaaS |
| ¥200/hr | ¥35,200/月 | エンタープライズ |

### インフラ原価（ワークスペース1時間あたり）

| 構成 | 原価/hr | ¥100課金時の粗利率 |
|-----|---------|------------------|
| **ECS on EC2 (c6i.xlarge, 0.5vCPU/1GiB)** | ¥3.7 (~$0.025) | **96%** |
| **ECS on EC2 + RI/SP 50%割引** | ¥1.9 (~$0.013) | **98%** |
| **ECS Fargate (0.5vCPU/1GiB)** | ¥4.2 (~$0.028) | **96%** |
| **E2B Pro (1vCPU)** | ¥7.5 (~$0.05) | **93%** |
| **Firecracker on C8i (0.5vCPU/512MiB)** | ¥1.4 (~$0.009) | **99%** |
| **Firecracker on bare metal** | ¥1.1 (~$0.007) | **99%** |

> **結論**: どの構成でも粗利率90%超。差が出るのは**同時接続数が数百を超えた時の月額総額**。

### 月額インフラコスト比較（同時接続数別）

| 同時接続数 | E2B Pro | ECS EC2 (OD) | ECS EC2 (RI) | Fargate | Firecracker C8i |
|-----------|---------|-------------|-------------|---------|----------------|
| 50 | $1,825 | $807 | $403 | $901 | $330 |
| 100 | $3,650 | $1,613 | $807 | $1,802 | $660 |
| 500 | $18,250 | $8,067 | $4,034 | $9,010 | $3,300 |
| 1,000 | $36,500 | $16,134 | $8,067 | $18,020 | $6,600 |

> **計算前提**: 24/7稼働、730hr/月。実際は利用時間帯に偏りがあるため、オートスケールで30-50%削減可能。

---

## 2. 各構成のトレードオフ

### 2.1 ECS on EC2（推奨: フェーズ1）

**利点**: 低コスト、任意Dockerイメージ、EC2 RI/Spot活用可、スケーリング実績豊富
**欠点**: gVisor非対応（ECS制約）、コンテナ起動2-15秒、運用中程度
**起動時間**: プリウォーム済みインスタンスで2-5秒

```
月額目安 (500同時, RI適用): ~$4,000/月
  → ¥100/hr課金 × 500人 × 176hr/月 = ¥8,800,000 売上
  → 粗利率 ~93%（インフラ外コスト含めても十分）
```

### 2.2 E2B Pro

**利点**: 運用ゼロ、150ms起動、SDK充実、すぐ始められる
**欠点**: 高い（500同時で$18K/月）、Debianのみ、最大1,100同時、ベンダーロックイン
**最適用途**: MVP検証、50同時以下の初期フェーズ

### 2.3 ECS Fargate

**利点**: EC2管理不要、Firecracker分離（強い）
**欠点**: コールドスタート20-60秒（対話型ワークスペースに致命的）、Docker socket不可、ENI制約
**最適用途**: バッチ処理、非対話型タスク

### 2.4 Firecracker on C8i（推奨: フェーズ3）

**利点**: 最安（$0.009/hr/VM）、125ms起動、ハードウェアVM分離
**欠点**: 運用複雑度が非常に高い（rootfs管理、カーネル更新、ネットワーク設定すべて自前）
**最適用途**: 500+同時、専任インフラチームがいる段階

---

## 3. 推奨フェーズ戦略

### Phase 1: 収益化開始（0-100同時）

```
構成: ECS on EC2 + Auto Scaling
理由: 最速で本番化でき、コストも低い
変更: Unix Socket → TCP通信、コンテナバックエンド抽象化
コスト: $200-1,600/月
```

**現アーキテクチャからの必要変更**:

| 変更箇所 | 内容 | 影響範囲 |
|---------|------|---------|
| 通信方式 | Unix Socket → TCP (socat経由は維持可) | `orchestrator.py` execute系 |
| コンテナ配置 | 単一Docker daemon → ECS Task Definition | `lifecycle.py`, `config.py` |
| セッション管理 | ローカル → Redis + ALB sticky session | 既にRedis利用済み |
| ウォームプール | ローカル → ECS Service desired count | `warm_pool.py` |

### Phase 2: スケール（100-500同時）

```
構成: ECS on EC2 + Reserved Instances + Spot混在
追加: SandboxProvider抽象化完了、gVisor検討（EKS移行時）
コスト: $1,600-4,000/月（RI適用後）
```

### Phase 3: 大規模（500+同時、必要時のみ）

```
構成: EKS + Kata Containers (Firecracker backend) on C8i
理由: Kubernetes + Firecracker分離の両立
コスト: $3,300+/月（EC2コストのみ）
前提: 専任インフラエンジニアの存在
```

---

## 4. ローカル開発 → AWS本番フロー

```
ローカル (Docker Compose)
  │  docker compose up → 全サービス起動
  │  ワークスペース: 通常Dockerコンテナ
  │
  ▼
ステージング (ECS on EC2, 小規模)
  │  Terraform/CDK → ECS Cluster + ALB
  │  ワークスペース: ECS Task (同じDockerイメージ)
  │
  ▼
本番 (ECS on EC2 + Auto Scaling)
     ワークスペース: ECS Task + RI/Spot最適化
```

**重要**: Dockerイメージは全環境で同一。差分は起動パラメータのみ。

---

## 5. 判断基準まとめ

| 判断軸 | 選択 |
|-------|------|
| **今すぐ始められるか** | ECS on EC2 > E2B > Firecracker |
| **月額コスト最安** | Firecracker C8i > ECS EC2 (RI) > Fargate > E2B |
| **運用負荷最小** | E2B > Fargate > ECS EC2 > Firecracker |
| **起動速度** | Firecracker (125ms) > E2B (150ms) > ECS EC2 (2-5s) > Fargate (20-60s) |
| **分離強度** | Firecracker = E2B = Fargate > gVisor > Docker |
| **収益性（500同時時）** | Firecracker > ECS EC2 (RI) > Fargate > E2B |

### 最終推奨

**Phase 1でECS on EC2を選択し、収益が安定したらPhase 3を検討する。**

- E2Bは運用が楽だが500同時で月$18Kは利益を圧迫する
- Firecrackerは最安だが運用チームが必要で初期投資が大きい
- ECS on EC2はバランスが良く、RI適用で十分安い
- 粗利率93-98%を維持でき、スケール時もコスト線形増加

---

## 参考: 詳細データソース

- 各構成の技術詳細 → `e2b-firecracker-sandbox-alternatives.md`（同ディレクトリ）
- E2B料金: https://e2b.dev/pricing
- ECS料金: https://aws.amazon.com/ecs/pricing/
- EC2 RI: https://aws.amazon.com/ec2/pricing/reserved-instances/
- Firecracker: https://github.com/firecracker-microvm/firecracker
- C8i nested virtualization: https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-ec2-nested-virtualization-on-virtual/
