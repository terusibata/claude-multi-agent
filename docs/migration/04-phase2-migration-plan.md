# Workspace Container Isolation - Phase 2 大規模移行計画書

**作成日**: 2026-02-07
**ステータス**: 計画策定完了・実装待ち
**前提**: Phase 1（コンテナ隔離 + セキュリティ基盤）全10ステップ完了済み
**対象ブランチ**: `claude/backend-migration-phase-2-VDZql`

---

## 1. Phase 2 の目的

Phase 1 で実現した基本的なコンテナ隔離の上に、**運用品質**・**セキュリティ強化**・**可観測性**を追加し、本番運用に耐えうるシステムに仕上げる。

### Phase 2 スコープ

| # | 項目 | カテゴリ | 概要 |
|---|------|---------|------|
| A | WarmPool最適化 | 運用品質 | マルチインスタンス安全性強化、プール監視、枯渇耐性 |
| B | userns-remap | セキュリティ | コンテナroot → ホスト非特権ユーザーへのマッピング |
| C | カスタムseccompプロファイル | セキュリティ | ホワイトリスト方式によるsyscall制限 |
| D | 監視ダッシュボード + アラート | 可観測性 | Prometheusメトリクス + Grafanaダッシュボード + アラートルール |
| E | Proxyレイテンシ最適化 | 運用品質 | コネクションプール、DNS キャッシュ、Keep-Alive最適化 |

---

## 2. Phase 1 完了時点のアーキテクチャ（現状）

```
Frontend ─── HTTPS ──→ Backend (FastAPI)
                         ├─ ContainerOrchestrator (aiodocker + Redis)
                         │   ├─ ContainerLifecycleManager (作成/破棄/ヘルスチェック)
                         │   ├─ WarmPoolManager (LPOP アトミック取得、補充)
                         │   └─ ContainerGarbageCollector (TTL/孤立コンテナ破棄)
                         ├─ CredentialInjectionProxy (per container)
                         │   ├─ Unix Socket listen
                         │   ├─ DomainWhitelist
                         │   ├─ SigV4注入
                         │   └─ CONNECT TLSパススルー
                         └─ WorkspaceFileSync (S3 ↔ Container)
                                │ Unix Socket
                                ▼
                        Container (--network none)
                          ├─ workspace_agent (FastAPI over UDS)
                          ├─ /opt/venv (プリインストール済み)
                          └─ /workspace (S3同期)
```

### Phase 1 セキュリティレイヤー（実装済み）

| Layer | 内容 | ステータス |
|-------|------|----------|
| L1 | Docker隔離 (namespace, cgroups) | 完了 |
| L3 | リソース制限 (CPU 2core / Mem 2GB / PIDs 100 / Disk 5GB) | 完了 |
| L4 | --network none (ネットワークIF排除) | 完了 |
| L5 | read-only rootfs + noexec tmpfs | 完了 |
| L6 | 認証情報隔離 (Proxy注入) | 完了 |

### Phase 2 で追加するセキュリティレイヤー

| Layer | 内容 | ステータス |
|-------|------|----------|
| L2 | seccomp（カスタムプロファイル、ホワイトリスト方式） | **Phase 2** |
| L7 | userns-remap (コンテナroot → ホスト非特権ユーザー) | **Phase 2** |

---

## 3. 現行実装の分析（Phase 2 対象箇所）

### 3.1 WarmPool の現行実装

**ファイル**: `app/services/container/warm_pool.py`

| 機能 | 実装状態 | Phase 2 改善点 |
|------|---------|---------------|
| LPOP アトミック取得 | 完了 | 十分（マルチインスタンス安全） |
| 不健全コンテナスキップ | 完了 | 維持 |
| 非同期補充 | 完了 | 補充失敗時のリトライロジック追加 |
| ドレイン | 完了 | 維持 |
| プール枯渇メトリクス | **未実装** | 枯渇回数・取得レイテンシの記録 |
| プール起動時プリヒート | **未実装** | アプリ起動時にmin_sizeまで充填 |
| プールサイズ自動調整 | **未実装** | 負荷パターンに応じた動的調整 |

### 3.2 セキュリティ設定の現行実装

**ファイル**: `app/services/container/config.py`

| 設定 | 実装状態 | Phase 2 改善点 |
|------|---------|---------------|
| seccomp | Dockerデフォルト（暗黙適用） | カスタムプロファイル (SCMP_ACT_ERRNO) |
| SecurityOpt | `no-new-privileges:true` | + seccompプロファイルパス追加 |
| userns-remap | **未実装** | Docker daemon設定 + 互換性検証 |

### 3.3 可観測性の現行実装

**ファイル**: `app/api/health.py`, `app/services/container/*.py`

| 項目 | 実装状態 | Phase 2 改善点 |
|------|---------|---------------|
| ヘルスチェック | DB/Redis/S3/Container | 維持 |
| WarmPoolサイズ表示 | health endpoint | 詳細メトリクス追加 |
| 構造化ログ | structlog | メトリクスタグ追加 |
| Prometheus メトリクス | **未実装** | /metricsエンドポイント新設 |
| Grafana ダッシュボード | **未実装** | JSON定義作成 |
| アラートルール | **未実装** | Prometheus Alertmanager |

### 3.4 Proxyの現行実装

**ファイル**: `app/services/proxy/credential_proxy.py`

| 機能 | 実装状態 | Phase 2 改善点 |
|------|---------|---------------|
| HTTPフォワード | httpx.AsyncClient | コネクションプーリング強化 |
| CONNECT TLSパススルー | asyncio TCP tunnel | 維持 |
| DNS解決 | 毎リクエスト | DNS キャッシュ導入 |
| Keep-Alive | httpx デフォルト | 明示的設定・最適化 |
| レイテンシ計測 | **未実装** | リクエスト毎の計測・ログ出力 |

---

## 4. Phase 2 タスク一覧

### Step 1: WarmPool最適化（マルチインスタンス対応強化）

| # | タスク | 依存 | 対象ファイル | 概要 |
|---|--------|------|-------------|------|
| 1.1 | 起動時プリヒート実装 | なし | `app/services/container/warm_pool.py`, `app/main.py` | アプリ起動時にmin_sizeまでプールを充填。起動遅延防止のため非同期で実行 |
| 1.2 | 補充リトライロジック追加 | なし | `app/services/container/warm_pool.py` | `_create_and_add()` 失敗時のexponential backoffリトライ（最大3回） |
| 1.3 | プールメトリクス収集 | 4.1 | `app/services/container/warm_pool.py` | 枯渇回数、ヒット率、取得レイテンシをPrometheusメトリクスとして公開 |
| 1.4 | プール枯渇アラート連携 | 1.3, 4.3 | `monitoring/alerts/` | 3回連続枯渇でアラート発火 |
| 1.5 | WarmPool設定のホットリロード | なし | `app/services/container/warm_pool.py`, `app/config.py` | min/max_sizeをRedis経由で動的変更可能にする |

### Step 2: userns-remap 導入

| # | タスク | 依存 | 対象ファイル | 概要 |
|---|--------|------|-------------|------|
| 2.1 | subordinate UID/GID マッピング定義 | なし | `deployment/docker/subuid`, `deployment/docker/subgid` | dockremap ユーザー用のUID/GIDレンジ定義 |
| 2.2 | Docker daemon設定ファイル作成 | 2.1 | `deployment/docker/daemon.json` | `{"userns-remap": "default", "storage-driver": "overlay2"}` |
| 2.3 | 既存コンテナとの互換性検証手順書 | 2.2 | `docs/migration/05-userns-remap-verification.md` | userns-remap有効化時の影響範囲と検証手順 |
| 2.4 | コンテナ設定のUID/GIDマッピング対応 | 2.2 | `app/services/container/config.py` | Bind mount権限やファイルオーナーの調整 |
| 2.5 | ソケットファイル権限の調整 | 2.4 | `app/services/container/lifecycle.py` | userns-remap下でのproxy.sock/agent.sockの権限設定 |
| 2.6 | デプロイメント手順書作成 | 2.1-2.5 | `docs/migration/06-userns-remap-deployment.md` | Docker再起動を伴うため、ローリングアップデート手順を明記 |

### Step 3: カスタムseccompプロファイル

| # | タスク | 依存 | 対象ファイル | 概要 |
|---|--------|------|-------------|------|
| 3.1 | 必要syscall調査・一覧作成 | なし | `docs/migration/07-seccomp-syscall-analysis.md` | Python 3.11 + Node.js 20 + pip + git + 一般的なデータ処理で必要なsyscallの特定 |
| 3.2 | seccompプロファイルJSON作成 | 3.1 | `deployment/seccomp/workspace-seccomp.json` | SCMP_ACT_ERRNO デフォルト + 許可syscallのホワイトリスト |
| 3.3 | プロファイル適用のコンテナ設定変更 | 3.2 | `app/services/container/config.py` | SecurityOpt に `seccomp=<profile-path>` を追加 |
| 3.4 | 設定ファイルパスの環境変数化 | 3.3 | `app/config.py` | `SECCOMP_PROFILE_PATH` 設定項目の追加 |
| 3.5 | 動作検証テストスイート | 3.3 | `tests/integration/test_seccomp.py` | pip install, python実行, Node.js実行, claude-code実行, git操作が正常動作することを確認 |
| 3.6 | seccomp違反ログの監視連携 | 3.5, 4.1 | `app/services/container/lifecycle.py` | コンテナログからseccomp違反(EPERM)を検出しメトリクス化 |

### Step 4: 監視ダッシュボード + アラート

| # | タスク | 依存 | 対象ファイル | 概要 |
|---|--------|------|-------------|------|
| 4.1 | Prometheusメトリクスライブラリ導入 | なし | `requirements.txt`, `app/infrastructure/metrics.py` | prometheus-client 追加、メトリクスレジストリ定義 |
| 4.2 | インフラメトリクス実装 | 4.1 | `app/infrastructure/metrics.py` | アクティブコンテナ数 (Gauge)、WarmPoolサイズ (Gauge)、ホストCPU/メモリ使用率 (Gauge) |
| 4.3 | アプリケーションメトリクス実装 | 4.1 | `app/infrastructure/metrics.py` | コンテナ起動時間 (Histogram, P95<10s)、リクエスト成功率 (Counter)、クラッシュ率 (Counter)、S3同期エラー率 (Counter)、Proxyレイテンシ (Histogram, P95<100ms)、ドメインブロック率 (Counter) |
| 4.4 | /metricsエンドポイント追加 | 4.1 | `app/api/metrics.py`, `app/main.py` | Prometheus scraping用のHTTPエンドポイント |
| 4.5 | メトリクス計測ポイントの埋め込み | 4.2, 4.3 | `app/services/container/orchestrator.py`, `app/services/container/warm_pool.py`, `app/services/proxy/credential_proxy.py`, `app/services/workspace/file_sync.py` | 各サービスのキーポイントにメトリクス収集コードを追加 |
| 4.6 | Grafanaダッシュボード定義 | 4.5 | `monitoring/grafana/dashboards/workspace-containers.json` | コンテナライフサイクル、WarmPool、Proxy、セキュリティの4パネルグループ |
| 4.7 | アラートルール定義 | 4.5 | `monitoring/prometheus/alerts/workspace-alerts.yml` | 下記アラート一覧参照 |
| 4.8 | docker-compose.yml監視スタック追加 | 4.6, 4.7 | `docker-compose.yml`, `monitoring/prometheus/prometheus.yml` | Prometheus + Grafana コンテナの追加（開発環境用） |

#### アラート一覧（Step 4.7）

| アラート名 | 条件 | Severity |
|-----------|------|----------|
| WarmPoolExhausted | 3回連続でプール空 | warning |
| ContainerStartupSlow | コンテナ起動P95 > 10秒（5分間） | warning |
| HighCrashRate | クラッシュ率 > 5%（5分間） | critical |
| LowRequestSuccessRate | リクエスト成功率 < 95%（5分間） | critical |
| S3SyncErrorHigh | S3同期エラー率 > 1%（10分間） | warning |
| ProxyLatencyHigh | Proxyレイテンシ P95 > 100ms（5分間） | warning |
| HighContainerCount | アクティブコンテナ数 > 最大値の80% | warning |
| GCFailure | GCサイクル連続失敗 > 3回 | critical |

### Step 5: Proxyレイテンシ最適化

| # | タスク | 依存 | 対象ファイル | 概要 |
|---|--------|------|-------------|------|
| 5.1 | ベースラインレイテンシ計測 | 4.5 | `tests/benchmark/` | 現状のProxy経由リクエストのP50/P95/P99レイテンシを計測・記録 |
| 5.2 | httpx コネクションプール最適化 | なし | `app/services/proxy/credential_proxy.py` | `httpx.AsyncClient` のプールサイズ・タイムアウト・Keep-Alive設定の最適化 |
| 5.3 | DNS キャッシュ導入 | なし | `app/services/proxy/dns_cache.py` | ホワイトリストドメインのDNS結果をTTL付きでキャッシュ |
| 5.4 | Proxy per-request レイテンシログ | 4.1 | `app/services/proxy/credential_proxy.py` | 各リクエストの処理時間をstructlogに出力 + Prometheusメトリクスに記録 |
| 5.5 | Proxy接続プール共有の検討・実装 | 5.2 | `app/services/proxy/credential_proxy.py`, `app/services/container/orchestrator.py` | 複数Proxyインスタンス間でhttpxクライアントプールを共有し、接続効率を向上 |
| 5.6 | 最適化後レイテンシ計測・比較 | 5.2-5.5 | `docs/migration/08-proxy-latency-report.md` | 最適化前後のレイテンシ比較レポート |

### Step 6: 統合テスト・検証

| # | タスク | 依存 | 対象ファイル | 概要 |
|---|--------|------|-------------|------|
| 6.1 | Phase 2 統合テスト作成 | 1-5全Step | `tests/integration/test_phase2.py` | WarmPool最適化・seccomp・メトリクスの結合テスト |
| 6.2 | セキュリティ検証レポート更新 | 2, 3 | `docs/migration/03-security-verification.md` | userns-remap・カスタムseccompのセキュリティレイヤー検証追記 |
| 6.3 | 負荷テスト実施 | 全Step | `tests/load/` | 同時接続30コンテナ、WarmPool枯渇シナリオ、Proxyスループットテスト |

### Step 7: ドキュメント整備

| # | タスク | 依存 | 対象ファイル | 概要 |
|---|--------|------|-------------|------|
| 7.1 | Phase 2 進捗管理ドキュメント更新 | 全Step | `docs/migration/01-progress-tracker.md` | Phase 2 全ステップの完了記録 |
| 7.2 | 運用手順書作成 | 4, 5 | `docs/operations/monitoring-guide.md` | 監視ダッシュボードの見方、アラート対応フロー |
| 7.3 | セキュリティ設定ガイド | 2, 3 | `docs/operations/security-config-guide.md` | userns-remap・seccompの有効化/無効化手順 |

---

## 5. 実装順序とクリティカルパス

```
Step 4.1 (Prometheus導入)
  │
  ├─→ Step 1 (WarmPool最適化) ─────────→ Step 1.3-1.4 (メトリクス連携)
  │
  ├─→ Step 4.2-4.8 (監視スタック全体) ─→ Step 5.1 (ベースライン計測)
  │                                         │
  │                                         ▼
  │                                     Step 5.2-5.5 (Proxy最適化)
  │                                         │
  │                                         ▼
  │                                     Step 5.6 (最適化結果レポート)
  │
  ├─→ Step 2 (userns-remap) ───────────→ Step 6.2 (セキュリティ検証更新)
  │
  ├─→ Step 3.1-3.2 (seccomp調査・作成)
  │     │
  │     ▼
  │   Step 3.3-3.4 (設定変更) ──────────→ Step 3.5 (動作検証)
  │                                         │
  │                                         ▼
  │                                     Step 3.6 (seccomp違反監視)
  │
  └─→ Step 6 (統合テスト・検証) ────────→ Step 7 (ドキュメント整備)
```

### 並列実行可能なグループ

| グループ | Steps | 依存関係 |
|---------|-------|---------|
| グループA | Step 1 (WarmPool) + Step 4.1 (Prometheus基盤) | なし（最初に着手） |
| グループB | Step 2 (userns-remap) + Step 3 (seccomp) | 独立して並列可能 |
| グループC | Step 4.2-4.8 (監視) + Step 5 (Proxy) | Step 4.1 完了後 |
| グループD | Step 6 (統合テスト) + Step 7 (ドキュメント) | 全グループ完了後 |

### クリティカルパス

Step 4.1 → Step 4.2-4.5 → Step 5.1 → Step 5.2-5.5 → Step 6.1 → Step 7.1

---

## 6. 変更対象ファイル一覧

### 6.1 新規作成ファイル

| ファイル | 目的 | Step |
|---------|------|------|
| `app/infrastructure/metrics.py` | Prometheusメトリクス定義・レジストリ | 4.1 |
| `app/api/metrics.py` | /metricsエンドポイント | 4.4 |
| `app/services/proxy/dns_cache.py` | DNSキャッシュ | 5.3 |
| `deployment/seccomp/workspace-seccomp.json` | カスタムseccompプロファイル | 3.2 |
| `deployment/docker/daemon.json` | userns-remap設定 | 2.2 |
| `deployment/docker/subuid` | subordinate UIDマッピング | 2.1 |
| `deployment/docker/subgid` | subordinate GIDマッピング | 2.1 |
| `monitoring/grafana/dashboards/workspace-containers.json` | Grafanaダッシュボード | 4.6 |
| `monitoring/prometheus/prometheus.yml` | Prometheus設定 | 4.8 |
| `monitoring/prometheus/alerts/workspace-alerts.yml` | アラートルール | 4.7 |
| `tests/integration/test_seccomp.py` | seccomp動作検証テスト | 3.5 |
| `tests/integration/test_phase2.py` | Phase 2統合テスト | 6.1 |
| `docs/migration/05-userns-remap-verification.md` | userns-remap互換性検証 | 2.3 |
| `docs/migration/06-userns-remap-deployment.md` | userns-remapデプロイ手順 | 2.6 |
| `docs/migration/07-seccomp-syscall-analysis.md` | seccomp syscall分析 | 3.1 |
| `docs/migration/08-proxy-latency-report.md` | Proxyレイテンシ比較レポート | 5.6 |
| `docs/operations/monitoring-guide.md` | 監視運用ガイド | 7.2 |
| `docs/operations/security-config-guide.md` | セキュリティ設定ガイド | 7.3 |

### 6.2 改修ファイル

| ファイル | 変更内容 | Step |
|---------|---------|------|
| `app/services/container/warm_pool.py` | プリヒート、リトライ、メトリクス計測 | 1.1-1.3 |
| `app/services/container/config.py` | seccompプロファイルパス、userns対応 | 2.4, 3.3 |
| `app/services/container/lifecycle.py` | ソケット権限調整、seccomp違反検出 | 2.5, 3.6 |
| `app/services/container/orchestrator.py` | メトリクス計測ポイント追加 | 4.5 |
| `app/services/proxy/credential_proxy.py` | コネクションプール最適化、レイテンシ計測 | 5.2, 5.4 |
| `app/services/workspace/file_sync.py` | S3同期メトリクス追加 | 4.5 |
| `app/config.py` | seccompパス、メトリクス設定追加 | 3.4, 4.1 |
| `app/main.py` | WarmPoolプリヒート呼び出し、メトリクスルーター登録 | 1.1, 4.4 |
| `docker-compose.yml` | Prometheus + Grafana追加 | 4.8 |
| `requirements.txt` | prometheus-client追加 | 4.1 |
| `docs/migration/01-progress-tracker.md` | Phase 2進捗追加 | 7.1 |
| `docs/migration/03-security-verification.md` | L2 seccomp + L7 userns検証追記 | 6.2 |

---

## 7. Phase 2 メトリクス設計

### 7.1 メトリクス一覧

```python
# app/infrastructure/metrics.py で定義予定

# --- Gauge（現在値） ---
workspace_active_containers       # アクティブコンテナ数
workspace_warm_pool_size          # WarmPoolサイズ
workspace_host_cpu_percent        # ホストCPU使用率
workspace_host_memory_percent     # ホストメモリ使用率

# --- Histogram（分布） ---
workspace_container_startup_seconds        # コンテナ起動時間
workspace_proxy_request_duration_seconds   # Proxyリクエスト処理時間
workspace_s3_sync_duration_seconds         # S3同期処理時間
workspace_warm_pool_acquire_seconds        # WarmPool取得時間

# --- Counter（累積） ---
workspace_requests_total           # リクエスト総数 (label: status=success|error|timeout)
workspace_container_crashes_total  # コンテナクラッシュ数
workspace_s3_sync_errors_total     # S3同期エラー数
workspace_proxy_blocked_total      # Proxyドメインブロック数
workspace_warm_pool_exhausted_total # WarmPool枯渇回数
workspace_seccomp_violations_total  # seccomp違反検出数
workspace_gc_cycles_total           # GCサイクル数 (label: result=success|error)
```

### 7.2 SLI/SLO 定義

| SLI | SLO | 計測方法 |
|-----|-----|---------|
| コンテナ起動時間 | P95 < 10秒 | `workspace_container_startup_seconds` |
| リクエスト成功率 | > 95% | `workspace_requests_total{status="success"}` / total |
| クラッシュ率 | < 5% | `workspace_container_crashes_total` / total |
| S3同期エラー率 | < 1% | `workspace_s3_sync_errors_total` / total |
| Proxyレイテンシ | P95 < 100ms | `workspace_proxy_request_duration_seconds` |

---

## 8. userns-remap 導入ガイドライン

### 8.1 前提条件

- Docker Engine 20.10+ （`userns-remap` サポート）
- ストレージドライバ: overlay2（XFS推奨）
- **デーモンレベル設定のため、Docker再起動が必要**

### 8.2 影響範囲

| 項目 | 影響 | 対応 |
|------|------|------|
| 既存コンテナ | 全て再作成が必要 | ローリングアップデート |
| Bind mount | ホスト側ファイルのUID/GIDがリマップされる | ソケットディレクトリの権限調整 |
| Docker volume | 自動対応 | 対応不要 |
| ベースイメージ | 再ビルド不要 | 対応不要 |

### 8.3 ロールバック手順

`daemon.json` から `userns-remap` を削除して Docker 再起動。コンテナは全て再作成。

---

## 9. カスタムseccomp設計方針

### 9.1 アプローチ

**デフォルトアクション**: `SCMP_ACT_ERRNO`（ブロック）
**許可対象**: Python/Node.js/pip/git/一般的なデータ処理に必要なsyscallのみ

### 9.2 主要な許可syscallカテゴリ

| カテゴリ | 代表的syscall | 用途 |
|---------|-------------|------|
| ファイルI/O | read, write, open, close, stat, fstat, lseek, mmap | 基本ファイル操作 |
| プロセス | clone, execve, wait4, exit_group, getpid | プロセス管理 |
| メモリ | mmap, mprotect, munmap, brk | メモリ管理 |
| ネットワーク | socket(AF_UNIX), connect, sendto, recvfrom | Unix Socket通信のみ |
| 時間 | clock_gettime, gettimeofday, nanosleep | 時間取得・スリープ |

### 9.3 明示的ブロック対象

| syscall | 理由 |
|---------|------|
| mount, umount2 | FS変更防止 |
| reboot | システム操作防止 |
| kexec_load | カーネル変更防止 |
| ptrace | デバッグ/メモリ読取防止 |
| socket(AF_INET/AF_INET6) | ネットワーク通信防止（--network noneと二重防御） |

---

## 10. リスクと緩和策

| リスク | 影響度 | 緩和策 |
|--------|-------|--------|
| userns-remap 有効化でDocker再起動が必要 | 高 | ローリングアップデート、メンテナンスウィンドウ設定 |
| カスタムseccompが一部ライブラリの動作をブロック | 高 | 段階的導入（まず監査モードで検証→ブロックモード適用） |
| Prometheus導入によるリソース消費増 | 中 | メトリクス数の最小化、scrape間隔の調整 |
| Proxyコネクションプール共有によるメモリ増 | 低 | プールサイズ上限設定 |
| seccompプロファイル更新漏れ（新ライブラリ追加時） | 中 | CI/CDでseccompテストを必須化 |

---

## 11. Phase 2 完了基準

| 基準 | 達成条件 |
|------|---------|
| WarmPool最適化 | プリヒート動作、枯渇メトリクス記録、リトライ動作確認 |
| userns-remap | デプロイ手順書完成、検証環境での動作確認 |
| カスタムseccomp | プロファイル作成、テスト通過、監査モード動作確認 |
| 監視ダッシュボード | Prometheus/Grafana動作、全SLIメトリクス表示、アラート発火テスト |
| Proxyレイテンシ | P95 < 100ms達成、最適化前後比較レポート |
| 統合テスト | Phase 2全機能の結合テスト通過 |
| ドキュメント | 運用ガイド・セキュリティ設定ガイド完成 |

---

## 12. Phase 3 以降のスコープ（参考）

| Phase | 内容 |
|-------|------|
| Phase 3 | gVisor (runsc)、AppArmorプロファイル、セキュリティ監査ログ集約、ペネトレーションテスト |
| Phase 4 | マルチホスト対応、Auto Scaling、Spotインスタンス、Firecracker microVM |
