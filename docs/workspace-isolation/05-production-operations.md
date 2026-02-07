# 5. 本番運用

## 5.1 スケーリング戦略

### 5.1.1 スケーリングモデル

```
┌──────────────────────────────────────────────────────────────┐
│                    スケーリング構成                            │
│                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │  Backend #1  │    │  Backend #2  │    │  Backend #N  │     │
│  │  (API)       │    │  (API)       │    │  (API)       │     │
│  │  + WarmPool  │    │  + WarmPool  │    │  + WarmPool  │     │
│  │  [5 sandbox] │    │  [5 sandbox] │    │  [5 sandbox] │     │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘     │
│         │                   │                   │              │
│  ┌──────▼───────────────────▼───────────────────▼───────┐    │
│  │                  Docker Host / ECS / K8s              │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  スケーリングルール:                                          │
│  - Backend: CPU 使用率 70% → スケールアウト                   │
│  - WarmPool: 各 Backend に local で管理                      │
│  - 最大同時サンドボックス: Backend数 × warm_pool_max          │
└──────────────────────────────────────────────────────────────┘
```

### 5.1.2 デプロイメントモデル比較

| モデル | 利点 | 欠点 | 推奨 |
|--------|------|------|------|
| **ECS + Docker-in-Docker** | AWS 統合、Fargate 可 | DinD のセキュリティ懸念 | 非推奨 |
| **ECS + EC2 (特権)** | Docker socket 直接アクセス | EC2 管理が必要 | **Phase 1 推奨** |
| **EKS + Kata/gVisor** | K8s エコシステム | 学習コスト高 | Phase 4 検討 |
| **EC2 直接** | シンプル、完全制御 | スケーリング手動 | 小規模向け |

### 5.1.3 推奨構成: ECS on EC2

```yaml
# ECS タスク定義（概要）
taskDefinition:
  family: ai-agent-backend
  networkMode: host
  volumes:
    - name: docker-socket
      host:
        sourcePath: /var/run/docker.sock
    - name: workspaces
      host:
        sourcePath: /var/lib/aiagent/workspaces

  containerDefinitions:
    - name: backend
      image: ai-agent-backend:latest
      mountPoints:
        - sourceVolume: docker-socket
          containerPath: /var/run/docker.sock
        - sourceVolume: workspaces
          containerPath: /var/lib/aiagent/workspaces
      environment:
        - name: SANDBOX_ENABLED
          value: "true"
```

### 5.1.4 キャパシティプランニング

```
1 Backend インスタンス (4 CPU / 8 GB RAM) の想定:
  - uvicorn ワーカー: 4
  - 同時サンドボックス: 最大 10
  - Warm Pool: 5 待機
  - 各サンドボックス: 1 CPU / 2 GB RAM (上限)
  - 実効利用: 通常 2-3 サンドボックスがアクティブ

必要なホストリソース:
  - CPU: 4 (Backend) + 10 (Sandbox max) = 14 cores
  - RAM: 8 GB (Backend) + 20 GB (Sandbox max) = 28 GB
  - 推奨インスタンス: c5.4xlarge (16 vCPU / 32 GB) or 相当

10 同時ユーザーの場合:
  - 2-3 Backend インスタンス
  - 合計 20-30 サンドボックスキャパシティ

100 同時ユーザーの場合:
  - 10-15 Backend インスタンス
  - 合計 100-150 サンドボックスキャパシティ
```

## 5.2 コンテナライフサイクル: クリーンアップ戦略と状態復元

### 5.2.1 クリーンアップ戦略の比較

| 戦略 | idle TTL | 長所 | 短所 |
|------|---------|------|------|
| **A: 短い TTL (1時間)** | 1h | リソース効率最良、セキュリティ窓が狭い | 再作成頻度が高い |
| **B: 長い TTL (24時間)** | 24h | 再作成ほぼ不要、UX最良 | リソース浪費大、セキュリティ窓が広い |
| **C: 固定時刻 (毎日0:00)** | N/A | 運用が予測可能 | バースト負荷、タイムゾーン問題 |
| **D: ハイブリッド (1h TTL + 状態復元)** | 1h | リソース効率◎、UX◎ | 復元ロジックの実装コスト |

### 5.2.2 推奨: ハイブリッド戦略 (D)

**1時間のアイドル TTL + S3 経由の状態復元**を推奨する。

```
┌──────────────────────────────────────────────────────────────────┐
│       ハイブリッド戦略: 短 TTL + 状態復元                         │
│                                                                  │
│  Message 1 (新規会話)                                             │
│    → Warm Pool or 新規コンテナ                                    │
│    → S3 → /work 同期                                             │
│    → SDK 実行                                                     │
│    → /work → S3 同期 (ファイル + pip state)  ← ★ pip 状態も保存   │
│    → コンテナ IDLE                                                │
│                                                                  │
│  (30分後) Message 2                                               │
│    → 同じコンテナ再利用（即時、pip 状態そのまま）                   │
│    → SDK 実行                                                     │
│    → /work → S3 同期                                             │
│    → コンテナ IDLE                                                │
│                                                                  │
│  (2時間後 = idle 1h 超過) コンテナ破棄                             │
│    → cleanup_expired() がコンテナを破棄                            │
│    → ローカルディレクトリ削除                                      │
│                                                                  │
│  (3時間後) Message 3                                              │
│    → 新規コンテナ作成 (Warm Pool or オンデマンド)                  │
│    → S3 → /work 同期 (ファイル + pip state)  ← ★ pip 状態を復元   │
│    → SDK 実行（pip install 済パッケージがそのまま使える）           │
│    → /work → S3 同期                                             │
│    → コンテナ IDLE                                                │
└──────────────────────────────────────────────────────────────────┘
```

**利点**:

- **リソース効率**: idle コンテナは最大1時間で回収される
- **UX**: 1時間以内の連続操作は即時応答（コンテナ再利用）
- **状態復元**: 1時間超の間隔でも pip install 等の状態は S3 から復元される
- **セキュリティ**: コンテナの生存期間が短く、攻撃窓が狭い

### 5.2.3 pip 状態の永続化設計

```
実行完了時の S3 同期:
  /work/
  ├── uploads/        → S3 同期 (既存)
  ├── output/         → S3 同期 (既存)
  └── .local/         → S3 同期 (★ 追加)
      └── lib/python3.11/site-packages/
          ├── pandas/
          ├── numpy/
          └── ...

S3 上のレイアウト:
  s3://{bucket}/workspaces/{tenant_id}/{conversation_id}/
  ├── uploads/...
  ├── output/...
  └── .local/...      ← pip install 済パッケージ
```

**同期方式**: `/work/.local/` ディレクトリを tar.gz にアーカイブしてS3に保存する。
個別ファイル同期ではなくアーカイブにする理由:

1. pip パッケージは数千ファイルになるため、個別同期はAPI呼び出しが多すぎる
2. アーカイブなら1回のPUT/GETで完結
3. 差分検出も容易（アーカイブのハッシュ比較）

```python
# 実行完了時
async def sync_pip_state_to_s3(workspace_path, tenant_id, conversation_id):
    pip_dir = Path(workspace_path) / ".local"
    if pip_dir.exists() and any(pip_dir.iterdir()):
        archive_path = f"/tmp/pip-state-{conversation_id}.tar.gz"
        # tar.gz に圧縮
        await asyncio.to_thread(shutil.make_archive, ...)
        # S3 にアップロード
        await s3.upload(tenant_id, conversation_id, "_pip_state.tar.gz", archive_path)

# コンテナ作成時
async def restore_pip_state_from_s3(workspace_path, tenant_id, conversation_id):
    if await s3.exists(tenant_id, conversation_id, "_pip_state.tar.gz"):
        archive = await s3.download(tenant_id, conversation_id, "_pip_state.tar.gz")
        # /work/.local に展開
        await asyncio.to_thread(shutil.unpack_archive, ...)
```

## 5.3 監視・オブザーバビリティ

### 5.3.1 メトリクス設計

```python
# app/services/sandbox/metrics.py

SANDBOX_METRICS = {
    # === プール状態 ===
    "sandbox_pool_size": Gauge(
        "sandbox_pool_size",
        "現在のプールサイズ（idle状態のサンドボックス数）",
    ),
    "sandbox_active_count": Gauge(
        "sandbox_active_count",
        "アクティブなサンドボックス数",
    ),
    "sandbox_pool_hit_rate": Counter(
        "sandbox_pool_hit_total",
        "Warm Pool ヒット/ミス",
        ["result"],  # hit, miss
    ),

    # === パフォーマンス ===
    "sandbox_acquire_duration": Histogram(
        "sandbox_acquire_duration_seconds",
        "サンドボックス取得のレイテンシ",
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
    ),
    "sandbox_execution_duration": Histogram(
        "sandbox_execution_duration_seconds",
        "サンドボックス内の実行時間",
        buckets=[1, 5, 10, 30, 60, 120, 300],
    ),

    # === リソース ===
    "sandbox_memory_usage": Gauge(
        "sandbox_memory_usage_bytes",
        "サンドボックスのメモリ使用量",
        ["container_id"],
    ),
    "sandbox_cpu_usage": Gauge(
        "sandbox_cpu_usage_percent",
        "サンドボックスのCPU使用率",
        ["container_id"],
    ),
    "sandbox_storage_usage": Gauge(
        "sandbox_storage_usage_bytes",
        "サンドボックスのストレージ使用量",
        ["container_id"],
    ),

    # === エラー ===
    "sandbox_error_total": Counter(
        "sandbox_error_total",
        "サンドボックスエラー数",
        ["error_type"],  # oom, timeout, crash, security_violation
    ),
    "sandbox_oom_killed_total": Counter(
        "sandbox_oom_killed_total",
        "OOM で強制終了されたサンドボックス数",
    ),

    # === セキュリティ ===
    "sandbox_seccomp_violation_total": Counter(
        "sandbox_seccomp_violation_total",
        "seccomp 違反の回数",
    ),
    "sandbox_apparmor_denial_total": Counter(
        "sandbox_apparmor_denial_total",
        "AppArmor 拒否の回数",
    ),
}
```

### 5.3.2 ログ設計

```python
# 構造化ログの例
{
    "event": "sandbox_execution",
    "level": "info",
    "timestamp": "2026-02-07T10:30:00Z",
    "sandbox": {
        "container_id": "abc123def456",
        "conversation_id": "conv-xxx-yyy",
        "tenant_id": "tenant-001",
        "status": "completed",
        "duration_ms": 15200,
        "pool_source": "warm_pool",  # warm_pool | on_demand
    },
    "resources": {
        "peak_memory_mb": 512,
        "cpu_seconds": 3.2,
        "storage_used_mb": 45,
        "processes_spawned": 12,
    },
    "security": {
        "seccomp_violations": 0,
        "apparmor_denials": 0,
        "network_mode": "none",
    }
}
```

### 5.3.3 アラートルール

| アラート | 条件 | 重要度 | アクション |
|---------|------|--------|-----------|
| Pool 枯渇 | pool_size == 0 が 30秒以上 | Critical | スケールアウト / Pool拡大 |
| OOM 頻発 | oom_killed > 5/5分 | High | メモリ制限の見直し |
| セキュリティ違反 | seccomp_violation > 0 | Critical | ログ調査 / テナント通知 |
| 実行タイムアウト | execution_duration > 5分 | Medium | 自動停止 / 通知 |
| コンテナクラッシュ | error_total 急増 | High | イメージ / 設定の確認 |
| ストレージ圧迫 | storage_usage > 80% | Medium | クリーンアップ / 拡張 |

### 5.3.4 監査ログ

```python
# 全てのサンドボックス操作を監査ログに記録
AUDIT_EVENTS = [
    "sandbox.created",          # コンテナ作成
    "sandbox.started",          # SDK 実行開始
    "sandbox.command_executed",  # Bash コマンド実行
    "sandbox.file_written",     # ファイル書き込み
    "sandbox.pip_install",      # pip install 実行
    "sandbox.network_access",   # ネットワークアクセス試行
    "sandbox.security_violation", # セキュリティ違反
    "sandbox.completed",        # 正常完了
    "sandbox.terminated",       # 強制終了
    "sandbox.error",            # エラー発生
]
```

## 5.3 障害復旧

### 5.3.1 障害シナリオと対応

| 障害 | 影響 | 検知 | 復旧手順 |
|------|------|------|---------|
| **サンドボックスクラッシュ** | 1セッション中断 | Docker イベント監視 | 自動: S3 から復元して再実行可能 |
| **Docker Daemon 停止** | 全サンドボックス停止 | ヘルスチェック失敗 | 自動: Backend がフォールバック実行 |
| **OOM Kill** | 1セッション中断 | cgroups OOM イベント | 自動: クライアントにエラー通知、再実行案内 |
| **ディスク枯渇** | 新規コンテナ作成不可 | ストレージ監視 | 手動: orphan コンテナ / イメージ清掃 |
| **Warm Pool 枯渇** | レイテンシ増加 | pool_size メトリクス | 自動: オンデマンド作成 + 補充 |
| **ネットワーク断** | S3 同期失敗 | 同期エラーログ | 自動: リトライ (指数バックオフ) |

### 5.4.2 データ保護 (セッション固定モデル)

```
サンドボックスのデータフロー（セッション固定）:

  S3 (永続ストレージ, source of truth)
    │
    │ 初回/復元時: sync_to_local (ファイル + pip state)
    ▼
  ローカルディスク (/var/lib/aiagent/workspaces/workspace_{conv_id})
    │
    │ bind mount
    ▼
  サンドボックスコンテナ (/work)   ← セッション中は維持
    │
    │ 毎メッセージ実行後: sync_from_local (ファイル + pip state)
    ▼
  S3 (永続ストレージ)

  ※ コンテナ破棄（TTL超過）後も S3 に全状態が保存済み
  ※ 次回メッセージ時に S3 から新コンテナに復元

データ保護ポイント:
  1. S3 が source of truth → コンテナ障害でもデータ喪失なし
  2. 毎メッセージ後に S3 同期 → 最新状態が常に永続化
  3. pip state も S3 に保存 → コンテナ再作成後も復元可能
  4. コンテナは一時的 → 障害時は S3 から完全復元
```

### 5.3.3 Orphan コンテナ対策

```python
class OrphanCleaner:
    """
    孤立サンドボックスコンテナのクリーンアップ

    Backend クラッシュ等でコンテナが残った場合に対応
    """

    async def cleanup_orphans(self) -> int:
        """孤立コンテナを検出して削除"""
        containers = self.docker_client.containers.list(
            filters={
                "label": "ai-agent.role=sandbox",
            },
            all=True,
        )

        cleaned = 0
        for container in containers:
            created = container.attrs["Created"]
            # max_lifetime を超えたコンテナを強制削除
            if self._is_expired(created):
                container.stop(timeout=5)
                container.remove(force=True)
                cleaned += 1

        return cleaned
```

## 5.4 コスト分析

### 5.4.1 リソースコスト比較

```
【現行】Backend コンテナ内で直接実行
  - EC2 (c5.xlarge): $0.17/hr × 24h × 30d = ~$122/月
  - 追加コストなし

【新設計】サンドボックスコンテナ
  - EC2 (c5.4xlarge): $0.68/hr × 24h × 30d = ~$489/月
    理由: サンドボックス用にCPU/メモリが追加で必要
  - または、Backend + Sandbox を分離デプロイ

  コスト増分: ~$367/月 (1ホストの場合)
```

### 5.4.2 コスト最適化策

| 施策 | 効果 | 実装難度 |
|------|------|---------|
| **Warm Pool サイズの自動調整** | -20% (トラフィックに応じて縮退) | 中 |
| **サンドボックスの共有** (同テナント) | -30% (コンテナ数削減) | 高 |
| **Spot Instance 活用** | -60% (EC2コスト) | 低 |
| **アイドルタイムアウト短縮** | -10% (idle コンテナ削減) | 低 |
| **軽量イメージ** (Alpine ベース) | -5% (起動速度改善 → Pool 効率化) | 低 |
| **リソース制限の最適化** | -15% (実使用量に合わせた制限) | 低 |

### 5.4.3 TCO (Total Cost of Ownership) 見積

```
小規模 (10 同時ユーザー):
  現行:    1 × c5.xlarge   = ~$122/月
  新設計:  1 × c5.4xlarge  = ~$489/月 (Spot: ~$195/月)
  増分:    +$367/月 (Spot: +$73/月)

中規模 (50 同時ユーザー):
  現行:    3 × c5.xlarge   = ~$367/月
  新設計:  3 × c5.4xlarge  = ~$1,468/月 (Spot: ~$587/月)
  増分:    +$1,101/月 (Spot: +$220/月)

大規模 (200 同時ユーザー):
  現行:    10 × c5.xlarge  = ~$1,224/月
  新設計:  10 × c5.4xlarge = ~$4,896/月 (Spot: ~$1,958/月)
  増分:    +$3,672/月 (Spot: +$734/月)

※ セキュリティインシデントの想定コスト: $10,000-$1,000,000+
   → 投資対効果は十分
```

## 5.5 運用チェックリスト

### 5.5.1 デプロイ前チェック

- [ ] サンドボックスイメージのビルドとテスト
- [ ] seccomp プロファイルの動作確認
- [ ] AppArmor プロファイルのインストールと確認
- [ ] Egress Proxy の許可リスト確認
- [ ] リソース制限値の負荷テスト
- [ ] Warm Pool の起動・停止テスト
- [ ] Docker socket のアクセス権限確認
- [ ] S3 同期の正常動作確認
- [ ] フォールバック（非サンドボックス実行）の動作確認
- [ ] 監視ダッシュボードのセットアップ
- [ ] アラートルールの設定

### 5.5.2 定常運用

- [ ] Orphan コンテナのクリーンアップ（日次）
- [ ] Docker イメージのアップデート（週次）
- [ ] seccomp / AppArmor 違反ログの確認（日次）
- [ ] リソース使用量トレンドの確認（週次）
- [ ] Warm Pool サイズの調整（月次）
- [ ] Egress Proxy 許可リストの見直し（月次）
- [ ] セキュリティパッチの適用（随時）

### 5.5.3 インシデント対応

```
Level 1 (自動復旧):
  - サンドボックスクラッシュ → 自動クリーンアップ、クライアントに通知
  - OOM Kill → エラー返却、再実行案内
  - Warm Pool 枯渇 → オンデマンド作成

Level 2 (運用者対応):
  - Docker Daemon 問題 → ホスト再起動
  - ディスク枯渇 → orphan 清掃、イメージ prune
  - 性能劣化 → スケールアウト、制限値調整

Level 3 (エスカレーション):
  - セキュリティ違反検出 → テナント通知、ログ分析
  - コンテナエスケープ疑い → 即時停止、フォレンジック
  - データ漏洩疑い → 影響範囲特定、通知、対策
```
