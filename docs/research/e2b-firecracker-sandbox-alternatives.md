# E2B・Firecracker・サンドボックス実行環境 調査レポート

> **調査日**: 2026年2月20日
> **目的**: AIエージェントのコード実行用サンドボックスとして、E2B (Firecracker) およびコスト効率の良い代替技術を包括的に調査

---

## エグゼクティブサマリー

本調査では、AIエージェントがユーザーコードを安全に実行するためのサンドボックス技術を9つのプラットフォームと7つの技術的アプローチから分析した。

### 最重要発見

1. **2026年2月12日、AWSが通常EC2インスタンスでネスト仮想化をサポート開始** — C8i/M8i/R8iインスタンスファミリーでFirecrackerが動作可能に。ベアメタル ($3.88/hr) → 通常インスタンス ($0.19/hr) で約20倍のコスト削減。
2. **gVisor (Docker + runsc)** が最もシンプルかつ低コストな分離強化手段。KVM不要、任意のEC2インスタンスで動作。
3. **E2B** がAIエージェント向けサンドボックスの事実上の標準。Firecracker分離、150ms起動、充実したSDK。
4. **分離レベルの選択が最重要決定事項**: Docker < gVisor < Firecracker の3段階。

---

## 目次

1. [分離技術の比較](#1-分離技術の比較)
2. [AIエージェントサービスのサンドボックス構成](#2-aiエージェントサービスのサンドボックス構成)
3. [コスト効率の良い代替技術](#3-コスト効率の良い代替技術)
4. [AWS上の各選択肢の詳細比較](#4-aws上の各選択肢の詳細比較)
5. [推奨アーキテクチャ（段階別）](#5-推奨アーキテクチャ段階別)
6. [参考資料](#6-参考資料)

---

## 1. 分離技術の比較

### 1.1 三つの分離レベル

| レベル | 技術 | 仕組み | 攻撃面 | 代表サービス |
|--------|------|--------|--------|-------------|
| **Docker (cgroup/namespace)** | 名前空間+cgroup | ホストカーネル共有 | カーネル脆弱性で脱出可能 | OpenHands, Replit, Daytona |
| **gVisor (syscall sandbox)** | ユーザ空間カーネル | syscallを傍受・再実装 | ホストカーネルの80%を隔離 | Modal, Google Cloud Run |
| **Firecracker (microVM)** | KVMベースの軽量VM | 専用カーネル/VM毎 | ハードウェアレベル分離 | E2B, AWS Lambda, Fargate, Fly.io |

### 1.2 判断基準

- **信頼できないコードを実行する場合** → Firecracker必須
- **自社エージェントの生成コードのみ** → gVisorで十分（defense-in-depth）
- **プロトタイプ/開発環境のみ** → Docker + セキュリティ設定で許容

---

## 2. AIエージェントサービスのサンドボックス構成

### 2.1 E2B — AIサンドボックスの事実上の標準

| 項目 | 詳細 |
|------|------|
| **分離技術** | Firecracker microVM |
| **起動時間** | ~150-200ms（コールドスタートなし） |
| **ネットワーク制御** | VPCベース、カスタム可能 |
| **SDK** | Python / TypeScript（全主要LLMフレームワーク対応） |
| **カスタム環境** | Dockerfileからカスタムサンドボックス構築可 |
| **OSSインフラ** | github.com/e2b-dev/infra（セルフホスト可能） |

**料金体系:**

| プラン | 月額基本 | 使用量課金 | セッション上限 | 同時実行 |
|--------|---------|-----------|---------------|---------|
| Hobby（無料） | $0 | $100クレジット付き | 1時間 | 20 |
| Pro | $150 | ~$0.05/hr (1vCPU) | 24時間 | 多数 |
| Enterprise | $3,000+ | カスタム | カスタム | カスタム |

**セルフホスト時のベンチマーク**: マネージドE2Bの**2.6倍高速**（ネットワークラウンドトリップ削減による）。

```python
# E2B SDK 使用例
from e2b_code_interpreter import Sandbox

sandbox = Sandbox()
execution = sandbox.run_code("print('hello world')")
print(execution.text)  # "hello world"
```

### 2.2 Modal — gVisorベースのサーバーレス

| 項目 | 詳細 |
|------|------|
| **分離技術** | gVisor (runsc) |
| **起動時間** | ~数百ms |
| **公表料金** | $0.047/vCPU-hr |
| **実効料金** | **$0.177/vCPU-hr**（リージョン1.25x × 非プリエンプション3x） |
| **GPU対応** | あり（主要な差別化要因） |

> **注意**: Modalの実効料金は公表料金の3.75倍。サンドボックス用途のCPUのみの場合、割高。GPU必要時のみ検討。

### 2.3 Fly.io Sprites — 永続的Firecracker VM

| 項目 | 詳細 |
|------|------|
| **分離技術** | Firecracker microVM |
| **料金** | $0.07/CPU-hr |
| **アイドル時** | **$0（課金なし）** |
| **チェックポイント/復元** | ~300ms |
| **永続ストレージ** | 100GBファイルシステム、プロセス・メモリ保持 |

> **特徴**: 他のエフェメラルサンドボックスと異なり、セッション間で完全な状態を保持。環境再構築コストがゼロ。

### 2.4 Daytona — 最新参入（2026年2月）

| 項目 | 詳細 |
|------|------|
| **分離技術** | Docker（デフォルト）、Firecracker（オプション） |
| **起動時間** | 27-90ms（コールドスタート） |
| **料金** | $0.067/hr（プラットフォーム手数料なし） |
| **資金調達** | Series A $24M（2026年2月） |

### 2.5 その他のサービス

| サービス | 分離技術 | 特記事項 |
|---------|---------|---------|
| **Devin (Cognition)** | 独自ハイパーバイザー "otterlink" | SaaSのみ、セルフホスト不可 |
| **Replit** | Docker + omegajail | 2層実行（高速/フル） |
| **OpenHands** | Docker コンテナ | OSS、ICLR 2025論文 |
| **Cursor/Windsurf** | ローカル seatbelt sandbox | 認証情報漏洩の脆弱性あり |
| **GitHub Copilot Agent** | GitHub Actions ランナー | 保守的な権限モデル |
| **Manus AI** | E2Bベース | Zero Trust、27ツール、フルVM |

---

## 3. コスト効率の良い代替技術

### 3.1 Docker + gVisor（最低コスト・最低複雑性）

**KVM不要** — 任意のEC2インスタンスで動作。

```bash
# インストール
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null
sudo apt-get update && sudo apt-get install -y runsc
```

```json
// /etc/docker/daemon.json
{
  "runtimes": {
    "runsc": {
      "path": "/usr/local/bin/runsc"
    }
  }
}
```

```bash
# 実行
docker run --runtime=runsc --network=none -it ubuntu /bin/bash
```

**パフォーマンス特性:**

| 項目 | オーバーヘッド | 備考 |
|------|-------------|------|
| CPU演算 | なし | ネイティブ実行 |
| syscall | 2-3倍遅い | Sentryで傍受 |
| ファイルI/O | 中程度 | VFS2/LISAFSで改善済 |
| ネットワーク | あり | ユーザ空間ネットワークスタック |
| 起動 | ミリ秒 | Dockerと同等 |

**本番実績**: Google Cloud Run、Cloud Functions、App Engine、GKE Sandbox で大規模稼働中。

### 3.2 AWS Lambda（マネージドFirecracker）

| 項目 | 詳細 |
|------|------|
| **分離** | Firecracker microVM（AWS管理） |
| **料金** | $0.0000166667/GB-秒 + $0.20/100万リクエスト |
| **無料枠** | 400,000 GB-秒/月（≈ 444回 × 15分@1GB） |
| **実行上限** | **15分（ハードリミット）** |
| **15分超の回避策** | Step Functions連携、Fargate自動オフロード |

**コスト試算（1GB、x86）:**

| シナリオ | 呼出/月 | 所要時間 | 月額 |
|---------|--------|---------|------|
| 軽量（ソロ開発） | 1,000 | 60秒 | ~$1 |
| 中量（本番） | 10,000 | 5分 | ~$50 |
| 大量（多数エージェント） | 50,000 | 15分 | ~$750 |

### 3.3 AWS Fargate（マネージドFirecracker、長時間向け）

| 項目 | 詳細 |
|------|------|
| **分離** | Firecracker microVM（AWS管理） |
| **料金** | ~$0.04/hr (1 vCPU) + $0.004/hr/GB RAM |
| **起動時間** | **30-60秒**（イメージプル含む） |
| **ネットワーク分離** | awsvpcのみ（プライベートサブネット + セキュリティグループで代替） |

> **注意**: 対話型コード実行には起動が遅すぎる。バッチ/バックグラウンドタスク向き。

### 3.4 EC2 C8i + ネスト仮想化 + Firecracker（2026年2月の画期的変更）

**2026年2月12日より**、C8i/M8i/R8iインスタンスでネスト仮想化が利用可能に。

| インスタンス | vCPU | RAM | 料金/hr | vs ベアメタル |
|-------------|------|-----|---------|-------------|
| c8i.xlarge | 4 | 8 GB | ~$0.187 | **~20倍安い** |
| c8i.2xlarge | 8 | 16 GB | ~$0.374 | ~10倍安い |
| m8i.xlarge | 4 | 16 GB | ~$0.21 | ~18倍安い |
| m8i.2xlarge | 8 | 32 GB | ~$0.42 | ~9倍安い |
| *.metal（従来） | 96+ | 192+ GB | $3.88-$10.80 | ベースライン |

**ネスト仮想化の追加コスト: ゼロ。**

Spotインスタンスやコンピュートセービングスプラン併用で $0.05-0.10/hr も可能。

### 3.5 Kata Containers（Kubernetes統合）

| 項目 | 詳細 |
|------|------|
| **仕組み** | Kubernetes RuntimeClassとして複数VMM（Firecracker/QEMU/Cloud Hypervisor）をラップ |
| **KVM必要** | はい（C8i/M8iのネスト仮想化、またはベアメタル） |
| **起動時間** | 150-300ms |
| **オーバーヘッド** | 5-10% |
| **適用場面** | Kubernetes上でPod単位のVM分離が必要な場合 |

---

## 4. AWS上の各選択肢の詳細比較

| ソリューション | 分離レベル | KVM必要? | 起動時間 | 最小EC2コスト | 複雑性 | 最適用途 |
|-------------|-----------|---------|---------|-------------|-------|---------|
| **Docker + gVisor** | syscall sandbox | No | ~ms | $0.05/hr (t3.medium) | 非常に低い | ソロ開発、クイックスタート |
| **AWS Lambda** | Firecracker (管理) | N/A | ~100ms (warm) | 従量課金 | 低い | 短時間タスク (<15分) |
| **AWS Fargate** | Firecracker (管理) | N/A | 30-60s | ~$0.04/hr | 低〜中 | バッチ/バックグラウンド |
| **EC2 C8i + Firecracker** | ハードウェアVM | Yes (ネスト) | ~125ms | ~$0.19/hr | 高い | 完全制御、強力な分離 |
| **E2B (管理)** | Firecracker | N/A | ~150ms | $150/月+使用量 | 非常に低い | AIエージェントサンドボックス |
| **Fly.io Sprites** | Firecracker | N/A | ~300ms (復元) | $0.07/CPU-hr | 低い | 永続的セッション |
| **Kata Containers** | ハードウェアVM | Yes | 150-300ms | ~$0.19/hr (C8i) | 高い | K8s + VM分離 |
| **ベアメタル Firecracker** | ハードウェアVM | Yes (ネイティブ) | ~125ms | ~$3.88/hr | 高い | 最高性能+分離 |

---

## 5. 推奨アーキテクチャ（段階別）

### Phase 1: ソロ開発者 / プロトタイプ

```
┌─────────────────────────────────────────────┐
│  EC2 t3.medium ($0.05/hr ≈ $36/月)          │
│  ┌─────────────────────────────────┐        │
│  │  Docker + gVisor (runsc)        │        │
│  │  --runtime=runsc --network=none │        │
│  │  ┌───────┐ ┌───────┐           │        │
│  │  │sandbox│ │sandbox│  ...       │        │
│  │  └───────┘ └───────┘           │        │
│  └─────────────────────────────────┘        │
└─────────────────────────────────────────────┘
```

- **コスト**: ~$36-60/月
- **分離**: syscallサンドボックス（gVisor）+ ネットワーク分離
- **代替案**: E2B Hobby（無料、$100クレジット）、AWS Lambda（無料枠）

### Phase 2: 小チーム / 初期プロダクション

```
┌──────────────────────────────────────┐
│           AWS Lambda / Fargate       │
│  (Firecracker管理、インフラ不要)      │
│  ┌────────┐ ┌────────┐ ┌────────┐   │
│  │microVM │ │microVM │ │microVM │   │
│  └────────┘ └────────┘ └────────┘   │
└──────────────────────────────────────┘
   or
┌──────────────────────────────────────┐
│           E2B Pro ($150/月)          │
│  Firecracker + SDK + 管理不要        │
└──────────────────────────────────────┘
```

- **コスト**: $50-200/月（Lambda/Fargate）、$150+使用量/月（E2B Pro）
- **分離**: ハードウェアレベル（Firecracker）
- **判断基準**: Lambda 15分制限に収まる → Lambda、それ以外 → E2B or Fargate

### Phase 3: スケーリング / 企業デプロイ

```
┌──────────────────────────────────────────────┐
│  EC2 C8i.2xlarge ($0.374/hr)                 │
│  ネスト仮想化 (KVM)                           │
│  ┌────────────────────────────────────────┐   │
│  │  Firecracker VMM                       │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐     │   │
│  │  │microVM │ │microVM │ │microVM │ ... │   │
│  │  │125ms起動│ │<5MB RAM│ │専用kernel│   │   │
│  │  └────────┘ └────────┘ └────────┘     │   │
│  └────────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```

- **コスト**: ~$0.19/hr（Spotで~$0.06/hr）
- **分離**: ハードウェアレベル（Firecracker + KVM）
- **注意**: C8i/M8i/R8iのみ対応（Intel Xeon 6プロセッサ）

### コスト比較サマリー

| 段階 | 使用量 | プラットフォーム | 月額コスト |
|------|-------|----------------|-----------|
| プロトタイプ | <1,000時間/月 | E2B Hobby (無料) or Docker+gVisor | $0-60 |
| 成長期 | 1,000-5,000時間/月 | E2B Pro or Lambda+Fargate | $150-885 |
| スケール期 | 5,000-10,000時間/月 | E2B Pro + Fly.io Sprites | $570-1,320 |
| 大規模 | 10,000+時間/月 | セルフホストFirecracker (C8i) | $400-3,000+ |

---

## 6. 参考資料

### 公式ドキュメント
- [gVisor ドキュメント](https://gvisor.dev/docs/)
- [gVisor パフォーマンスガイド](https://gvisor.dev/docs/architecture_guide/performance/)
- [gVisor プラットフォームガイド](https://gvisor.dev/docs/user_guide/platforms/)
- [Firecracker GitHub](https://github.com/firecracker-microvm/firecracker)
- [E2B ドキュメント](https://e2b.dev/docs)
- [E2B インフラ GitHub](https://github.com/e2b-dev/infra)

### AWS
- [EC2 ネスト仮想化ドキュメント](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html)
- [EC2 ネスト仮想化発表 (2026/02)](https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-ec2-nested-virtualization-on-virtual/)
- [EC2 C8i インスタンスタイプ](https://aws.amazon.com/ec2/instance-types/c8i/)
- [Fargate 料金](https://aws.amazon.com/fargate/pricing/)
- [Lambda 料金](https://aws.amazon.com/lambda/pricing/)
- [EC2 オンデマンド料金](https://aws.amazon.com/ec2/pricing/on-demand/)

### 比較・分析
- [Kata vs Firecracker vs gVisor — Edera](https://edera.dev/stories/kata-vs-firecracker-vs-gvisor-isolation-compared)
- [Kata vs Firecracker vs gVisor — Northflank](https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor)
- [AWS Containers Blog: Kata on EKS](https://aws.amazon.com/blogs/containers/enhancing-kubernetes-workload-isolation-and-security-using-kata-containers/)
- [E2B 料金見積もりツール](https://pricing.e2b.dev/)
- [Seven Years of Firecracker — Marc Brooker](https://brooker.co.za/blog/2025/09/18/firecracker.html)
- [AI Sandbox Benchmark 2026 — Superagent](https://www.superagent.sh/blog/ai-code-sandbox-benchmark-2026)
- [Field Guide to Sandboxes for AI](https://www.luiscardoso.dev/blog/sandboxes-for-ai)

### その他
- [Running Untrusted Code with Lambda — AWS Fundamentals](https://awsfundamentals.com/blog/sandboxing-with-aws-lambda)
- [Self-Hostable E2B Alternatives — Northflank](https://northflank.com/blog/self-hostable-alternatives-to-e2b-for-ai-agents)
- [VGS: gVisor on EKS](https://www.verygoodsecurity.com/blog/posts/secure-compute-part-2)
- [The Register: AWS Nested Virtualization](https://www.theregister.com/2026/02/17/nested_virtualization_aws_ec2/)
- [SkyPilot Self-Hosted LLM Sandbox](https://blog.skypilot.co/skypilot-llm-sandbox/)
