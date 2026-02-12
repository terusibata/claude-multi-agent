# 監視ダッシュボード 運用ガイド

**作成日**: 2026-02-07
**対象**: Phase 2 - Step 7.2 (監視運用ガイド)
**前提**: 監視スタック（Prometheus + Grafana）がデプロイ済みであること

---

## 1. ダッシュボードアクセス

| サービス | URL | 用途 |
|---------|-----|------|
| Grafana | http://localhost:3002 | 監視ダッシュボード |
| Prometheus | http://localhost:9090 | メトリクス収集・クエリ |
| アプリケーション /metrics | http://localhost:8000/metrics | メトリクスエンドポイント |

> **注**: アラート管理が必要な場合は Alertmanager を別途デプロイしてください。現在の docker-compose.yml には Prometheus + Grafana のみが含まれています。

---

## 2. 監視スタックの起動・停止

### 2.1 起動

```bash
# 監視スタック（Prometheus + Grafana）を起動
docker compose --profile monitoring up -d

# 起動確認
docker compose --profile monitoring ps
```

### 2.2 停止

```bash
# 監視スタックを停止
docker compose --profile monitoring down
```

### 2.3 トラブルシューティング

```bash
# Prometheus のログ確認
docker compose --profile monitoring logs prometheus

# Grafana のログ確認
docker compose --profile monitoring logs grafana

# メトリクスエンドポイントの直接確認
curl -s http://localhost:8000/metrics | head -50
```

---

## 3. ダッシュボードパネル説明

Grafanaダッシュボード「Workspace Containers」は4つの行（Row）で構成されています。

### 3.1 Row 1: Container Lifecycle（コンテナライフサイクル）

| パネル | 種類 | メトリクス | 説明 |
|--------|------|----------|------|
| Active Containers | Gauge | `workspace_active_containers` | 現在アクティブなワークスペースコンテナ数。急増・急減に注意 |
| Container Startup Time (P95) | Graph | `histogram_quantile(0.95, workspace_container_startup_seconds_bucket)` | コンテナ起動時間のP95値。SLO: 10秒以下 |
| Container Startup Time Distribution | Heatmap | `workspace_container_startup_seconds_bucket` | 起動時間の分布。異常な外れ値の検出に有用 |
| Container Crashes | Counter | `rate(workspace_container_crashes_total[5m])` | コンテナクラッシュの発生率。5分間のレートで表示 |
| Request Success Rate | Graph | `rate(workspace_requests_total{status="success"}[5m]) / rate(workspace_requests_total[5m]) * 100` | リクエスト成功率（%）。SLO: 95%以上 |

**確認ポイント**:
- アクティブコンテナ数が最大値の80%を超えていないか
- 起動時間が10秒を超える傾向がないか
- クラッシュが連続して発生していないか

### 3.2 Row 2: WarmPool（ウォームプール）

| パネル | 種類 | メトリクス | 説明 |
|--------|------|----------|------|
| WarmPool Size | Gauge | `workspace_warm_pool_size` | 現在のプールサイズ。min_size以上であること |
| WarmPool Exhaustion Events | Counter | `rate(workspace_warm_pool_exhausted_total[5m])` | プール枯渇の発生率。0が理想 |
| WarmPool Acquire Latency (P95) | Graph | `histogram_quantile(0.95, workspace_warm_pool_acquire_seconds_bucket)` | プールからのコンテナ取得時間。通常数ミリ秒 |
| WarmPool Hit Rate | Graph | `1 - (rate(workspace_warm_pool_exhausted_total[5m]) / rate(workspace_warm_pool_acquire_seconds_count[5m])) * 100` | プールヒット率（%）。100%が理想。枯渇回数と取得総数から算出 |

**確認ポイント**:
- プールサイズが0になっていないか（枯渇状態）
- 枯渇イベントが頻発していないか
- 取得レイテンシが異常に高くないか

### 3.3 Row 3: Proxy（プロキシ）

| パネル | 種類 | メトリクス | 説明 |
|--------|------|----------|------|
| Proxy Request Rate | Graph | `rate(workspace_proxy_request_duration_seconds_count[5m])` | Proxyを経由するリクエストレート |
| Proxy Latency (P95) | Graph | `histogram_quantile(0.95, workspace_proxy_request_duration_seconds_bucket)` | Proxyレイテンシ P95。SLO: 100ms以下 |
| Proxy Latency Distribution | Heatmap | `workspace_proxy_request_duration_seconds_bucket` | レイテンシの分布。異常値の検出に有用 |
| Domain Blocked Rate | Counter | `rate(workspace_proxy_blocked_total[5m])` | ドメインホワイトリストによるブロック率。不正リクエストの検出 |
| S3 Sync Errors | Counter | `rate(workspace_s3_sync_errors_total[5m])` | S3同期エラーの発生率。SLO: 1%以下 |

**確認ポイント**:
- レイテンシが100msを超える傾向がないか
- ドメインブロックが急増していないか（攻撃の兆候の可能性）
- S3同期エラーが継続していないか

### 3.4 Row 4: Security & GC（セキュリティ＆ガベージコレクション）

| パネル | 種類 | メトリクス | 説明 |
|--------|------|----------|------|
| Seccomp Violations | Counter | `rate(workspace_seccomp_violations_total[5m])` | seccompプロファイル違反の発生率。0が理想 |
| GC Cycles | Counter | `rate(workspace_gc_cycles_total[5m])` | GCサイクルの実行率（成功/失敗別） |
| GC Failure Rate | Graph | `rate(workspace_gc_cycles_total{result="error"}[5m]) / rate(workspace_gc_cycles_total[5m]) * 100` | GC失敗率。0%が理想 |
| Host CPU Usage | Gauge | `workspace_host_cpu_percent` | ホストCPU使用率 |
| Host Memory Usage | Gauge | `workspace_host_memory_percent` | ホストメモリ使用率 |

**確認ポイント**:
- seccomp違反が発生していないか（発生時はプロファイル見直し or 攻撃検知）
- GCが連続失敗していないか
- ホストリソースが逼迫していないか

---

## 4. SLI/SLO 定義

| SLI | SLO | メトリクス | 計算式 | アラート閾値 |
|-----|-----|----------|--------|------------|
| コンテナ起動時間 | P95 < 10秒 | `workspace_container_startup_seconds` | `histogram_quantile(0.95, ...)` | > 10秒が5分間継続 |
| リクエスト成功率 | > 95% | `workspace_requests_total` | `success / total * 100` | < 95%が5分間継続 |
| クラッシュ率 | < 5% | `workspace_container_crashes_total` | `crashes / total * 100` | > 5%が5分間継続 |
| S3同期エラー率 | < 1% | `workspace_s3_sync_errors_total` | `errors / total * 100` | > 1%が10分間継続 |
| Proxyレイテンシ | P95 < 100ms | `workspace_proxy_request_duration_seconds` | `histogram_quantile(0.95, ...)` | > 100msが5分間継続 |

---

## 5. アラート対応プレイブック

### 5.1 WarmPoolExhausted（WarmPool枯渇）

**Severity**: warning
**条件**: 3回連続でプールが空

**対応手順**:

1. 現在のプールサイズを確認
   ```bash
   curl -s http://localhost:8000/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['container_system']['warm_pool_size'])"
   ```
2. コンテナ作成ログを確認（エラーがないか）
   ```bash
   docker compose logs app | grep -i "warm_pool\|container_create" | tail -20
   ```
3. Dockerリソースの状態を確認
   ```bash
   docker system df
   docker info --format '{{.ContainersRunning}}/{{.Containers}}'
   ```
4. ホストリソースの確認
   ```bash
   free -h
   df -h
   ```
5. 必要に応じてWarmPoolのmin_sizeを調整、またはホストリソースを追加

---

### 5.2 ContainerStartupSlow（コンテナ起動遅延）

**Severity**: warning
**条件**: コンテナ起動P95 > 10秒が5分間継続

**対応手順**:

1. 起動時間の詳細確認
   ```promql
   histogram_quantile(0.95, rate(workspace_container_startup_seconds_bucket[5m]))
   ```
2. Docker daemonの状態確認
   ```bash
   sudo systemctl status docker
   docker info
   ```
3. ディスクI/Oの確認（overlay2のパフォーマンス影響）
   ```bash
   iostat -x 1 5
   ```
4. イメージキャッシュの状態確認
   ```bash
   docker images workspace-base
   ```
5. 原因に応じた対処:
   - ディスクI/O高負荷 → 不要なコンテナ/イメージの清掃
   - イメージ未キャッシュ → `docker pull` で事前取得
   - Docker daemon問題 → `systemctl restart docker`（要メンテナンスウィンドウ）

---

### 5.3 HighCrashRate（高クラッシュ率）

**Severity**: critical
**条件**: クラッシュ率 > 5%が5分間継続

**対応手順**:

1. クラッシュしたコンテナのログを確認
   ```bash
   docker compose logs app | grep -i "crash\|error\|exception" | tail -30
   ```
2. 直近で停止したコンテナの確認
   ```bash
   docker ps -a --filter "status=exited" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}" | head -20
   ```
3. OOMKill の確認
   ```bash
   dmesg | grep -i "oom\|killed" | tail -10
   ```
4. seccomp違反の確認（新しいsyscallがブロックされていないか）
   ```bash
   dmesg | grep seccomp | tail -10
   ```
5. 原因に応じた対処:
   - OOMKill → メモリ制限の見直し
   - seccomp違反 → プロファイルの更新
   - アプリケーションバグ → ログ分析、修正デプロイ

---

### 5.4 LowRequestSuccessRate（低リクエスト成功率）

**Severity**: critical
**条件**: リクエスト成功率 < 95%が5分間継続

**対応手順**:

1. エラーの内訳を確認
   ```promql
   rate(workspace_requests_total[5m]) by (status)
   ```
2. アプリケーションログのエラーを確認
   ```bash
   docker compose logs app | grep -i "error\|exception\|500" | tail -30
   ```
3. 外部依存サービスの確認
   ```bash
   # Redis
   redis-cli ping
   # S3（ヘルスチェック経由）
   curl -s http://localhost:8000/health | python3 -m json.tool
   ```
4. コンテナ作成の成功率を確認
   ```promql
   rate(workspace_container_startup_seconds_count[5m])
   ```
5. 原因に応じた対処:
   - Redis障害 → Redis復旧
   - コンテナ作成失敗 → Docker/ホストリソース確認
   - アプリケーションエラー → ログ分析、修正デプロイ

---

### 5.5 S3SyncErrorHigh（S3同期エラー高発生率）

**Severity**: warning
**条件**: S3同期エラー率 > 1%が10分間継続

**対応手順**:

1. S3同期エラーの詳細確認
   ```bash
   docker compose logs app | grep -i "s3\|sync\|boto" | tail -30
   ```
2. AWS認証情報の有効性確認
   ```bash
   # Proxy経由のAWS接続確認（ヘルスチェック）
   curl -s http://localhost:8000/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('s3', 'N/A'))"
   ```
3. S3バケットのアクセス確認
   ```bash
   aws s3 ls s3://<bucket-name>/ --max-items 1
   ```
4. ネットワーク接続の確認
   ```bash
   curl -s -o /dev/null -w '%{http_code}' https://s3.amazonaws.com/
   ```
5. 原因に応じた対処:
   - 認証エラー → 認証情報の更新
   - ネットワーク障害 → ネットワーク復旧確認
   - バケット権限 → IAMポリシー確認

---

### 5.6 ProxyLatencyHigh（Proxyレイテンシ高）

**Severity**: warning
**条件**: Proxyレイテンシ P95 > 100msが5分間継続

**対応手順**:

1. レイテンシの詳細確認
   ```promql
   histogram_quantile(0.95, rate(workspace_proxy_request_duration_seconds_bucket[5m]))
   histogram_quantile(0.99, rate(workspace_proxy_request_duration_seconds_bucket[5m]))
   ```
2. Proxy接続プールの状態確認
   ```bash
   docker compose logs app | grep -i "proxy\|connection_pool\|httpx" | tail -20
   ```
3. DNS解決時間の確認
   ```bash
   dig bedrock-runtime.us-east-1.amazonaws.com +stats | grep "Query time"
   ```
4. Bedrock APIの応答時間確認
   ```bash
   # Proxyを経由しない直接リクエストの時間測定
   time curl -s -o /dev/null https://bedrock-runtime.us-east-1.amazonaws.com/
   ```
5. 原因に応じた対処:
   - DNS遅延 → DNSキャッシュの確認・再起動
   - Bedrock API遅延 → AWSステータス確認、リージョン検討
   - コネクションプール枯渇 → プールサイズの調整

---

### 5.7 HighContainerCount（コンテナ数高）

**Severity**: warning
**条件**: アクティブコンテナ数が最大値の80%を超過

**対応手順**:

1. アクティブコンテナの確認
   ```bash
   docker ps --filter "label=workspace" | wc -l
   ```
2. TTL超過コンテナの確認（GCが正常動作しているか）
   ```bash
   docker compose logs app | grep -i "gc\|garbage\|ttl" | tail -20
   ```
3. 孤立コンテナの確認
   ```bash
   docker compose logs app | grep -i "orphan" | tail -10
   ```
4. 必要に応じてGCの手動実行、または最大コンテナ数の引き上げ

---

### 5.8 GCFailure（GC失敗）

**Severity**: critical
**条件**: GCサイクル連続失敗 > 3回

**対応手順**:

1. GCログの確認
   ```bash
   docker compose logs app | grep -i "gc\|garbage_collector" | tail -30
   ```
2. Docker APIの応答確認
   ```bash
   docker ps -q | wc -l
   curl -s --unix-socket /var/run/docker.sock http://localhost/containers/json | python3 -m json.tool | head -20
   ```
3. Redis接続の確認（GCはRedis経由でコンテナ情報を管理）
   ```bash
   redis-cli ping
   redis-cli info clients
   ```
4. 原因に応じた対処:
   - Docker API障害 → Docker daemon再起動（要メンテナンスウィンドウ）
   - Redis障害 → Redis復旧
   - アプリケーションバグ → ログ分析、修正デプロイ

---

## 6. トラブルシューティング用 Prometheusクエリ

### 6.1 コンテナライフサイクル

```promql
# アクティブコンテナ数の推移（1時間）
workspace_active_containers

# コンテナ起動時間のパーセンタイル
histogram_quantile(0.50, rate(workspace_container_startup_seconds_bucket[5m]))
histogram_quantile(0.95, rate(workspace_container_startup_seconds_bucket[5m]))
histogram_quantile(0.99, rate(workspace_container_startup_seconds_bucket[5m]))

# 起動成功率（5分間）
rate(workspace_container_startup_seconds_count[5m])

# クラッシュ率
rate(workspace_container_crashes_total[5m]) / rate(workspace_requests_total[5m]) * 100
```

### 6.2 WarmPool

```promql
# プールサイズ推移
workspace_warm_pool_size

# 枯渇イベント数（1時間累計）
increase(workspace_warm_pool_exhausted_total[1h])

# プール取得レイテンシ P95
histogram_quantile(0.95, rate(workspace_warm_pool_acquire_seconds_bucket[5m]))
```

### 6.3 Proxy

```promql
# Proxyリクエストレート（Histogram の _count から算出）
rate(workspace_proxy_request_duration_seconds_count[5m])

# Proxyレイテンシ（パーセンタイル別）
histogram_quantile(0.50, rate(workspace_proxy_request_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(workspace_proxy_request_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(workspace_proxy_request_duration_seconds_bucket[5m]))

# ドメインブロック率
rate(workspace_proxy_blocked_total[5m])

# S3同期エラー数（/5分）
rate(workspace_s3_sync_errors_total[5m])
```

### 6.4 セキュリティ & GC

```promql
# seccomp違反数（1時間累計）
increase(workspace_seccomp_violations_total[1h])

# GC成功率
rate(workspace_gc_cycles_total{result="success"}[5m]) / rate(workspace_gc_cycles_total[5m]) * 100

# GC失敗数（1時間累計）
increase(workspace_gc_cycles_total{result="error"}[1h])
```

### 6.5 ホストリソース

```promql
# CPU使用率
workspace_host_cpu_percent

# メモリ使用率
workspace_host_memory_percent
```

---

## 7. 定期確認事項

| 頻度 | 確認内容 |
|------|---------|
| 毎日 | ダッシュボード全パネルの概要確認、アラート発火履歴の確認 |
| 毎週 | SLI/SLOのトレンド分析、異常パターンの調査 |
| 毎月 | キャパシティプランニング、閾値の見直し、アラートルールの調整 |
| 四半期 | ダッシュボード・アラートの棚卸し、メトリクスの追加・削除検討 |
