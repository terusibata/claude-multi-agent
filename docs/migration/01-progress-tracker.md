# Workspace Container Isolation - 移行進捗管理

**最終更新**: 2026-02-07
**全体進捗**: Step 10/10 完了

---

## 進捗サマリー

| Step | タスク | ステータス | 備考 |
|------|--------|-----------|------|
| 1 | 基盤準備 | :white_check_mark: 完了 | 依存パッケージ・設定・モデル |
| 2 | ベースイメージ | :white_check_mark: 完了 | Dockerfile + requirements |
| 3 | workspace_agent | :white_check_mark: 完了 | コンテナ内FastAPI |
| 4 | Credential Injection Proxy | :white_check_mark: 完了 | ドメインWL + SigV4 |
| 5 | Container Orchestrator | :white_check_mark: 完了 | aiodocker + Redis |
| 6 | ファイル同期 | :white_check_mark: 完了 | S3 <-> Container |
| 7 | 実行エンジン書き換え | :white_check_mark: 完了 | ExecuteService全面改修 |
| 8 | アプリケーション統合 | :white_check_mark: 完了 | main.py + docker-compose |
| 9 | セキュリティ検証 | :white_check_mark: 完了 | 認証情報除去・多層防御確認 |
| 10 | クリーンアップ | :white_check_mark: 完了 | 旧コード削除・ドキュメント |

---

## 詳細進捗

### Step 1: 基盤準備

- [x] 1.1 依存パッケージ追加（aiodocker等）→ `requirements.txt`
- [x] 1.2 コンテナ関連設定の追加 → `app/config.py`
- [x] 1.3 ContainerInfo等のデータモデル定義 → `app/services/container/models.py`
- [x] 1.4 コンテナ作成設定の定義 → `app/services/container/config.py`

### Step 2: ワークスペースベースイメージ

- [x] 2.1 ベースイメージDockerfile作成 → `workspace-base/Dockerfile`
- [x] 2.2 コンテナ内Python依存パッケージ定義 → `workspace-base/workspace-requirements.txt`

### Step 3: コンテナ内エージェント（workspace_agent）

- [x] 3.1 リクエスト/レスポンスモデル定義 → `workspace_agent/models.py`
- [x] 3.2 Claude SDKクライアントラッパー → `workspace_agent/sdk_client.py`
- [x] 3.3 FastAPI メインアプリ（UDS対応） → `workspace_agent/main.py`

### Step 4: Credential Injection Proxy

- [x] 4.1 ドメインホワイトリスト → `app/services/proxy/domain_whitelist.py`
- [x] 4.2 AWS SigV4署名ユーティリティ → `app/services/proxy/sigv4.py`
- [x] 4.3 CredentialInjectionProxy本体 → `app/services/proxy/credential_proxy.py`

### Step 5: Container Orchestrator

- [x] 5.1 コンテナライフサイクル管理 → `app/services/container/lifecycle.py`
- [x] 5.2 ContainerOrchestrator本体 → `app/services/container/orchestrator.py`
- [x] 5.3 WarmPoolManager → `app/services/container/warm_pool.py`
- [x] 5.4 GC（ガベージコレクター）→ `app/services/container/gc.py`

### Step 6: ファイル同期

- [x] 6.1 Container ↔ S3ファイル同期 → `app/services/workspace/file_sync.py`

### Step 7: 実行エンジン書き換え

- [x] 7.1 ExecuteService全面書き換え → `app/services/execute_service.py`
- [x] 7.2 ストリーミングAPI改修 → `app/api/conversations.py`

### Step 8: アプリケーション統合

- [x] 8.1 main.pyにOrchestrator/GC統合 → `app/main.py`
- [x] 8.2 ヘルスチェックにコンテナ状態追加 → `app/api/health.py`
- [x] 8.3 docker-compose.yml更新 → `docker-compose.yml`
- [x] 8.4 ホストDockerfile更新 → `Dockerfile`

### Step 9: セキュリティ検証

- [x] 9.1 AWS認証情報の環境変数からの除去 → `app/main.py`
- [x] 9.2 セキュリティレイヤー確認 → `docs/migration/03-security-verification.md`

### Step 10: クリーンアップ

- [x] 10.1 不要コード削除 → `app/services/execute/` ディレクトリ削除
- [x] 10.2 AWSConfig移動 → `app/services/aws_config.py` に移動
- [x] 10.3 ドキュメント更新 → `docs/migration/`

---

## 変更ファイル一覧

### 新規作成

| ファイル | 目的 |
|---------|------|
| `workspace-base/Dockerfile` | コンテナベースイメージ |
| `workspace-base/workspace-requirements.txt` | コンテナ内Python依存 |
| `workspace_agent/__init__.py` | パッケージ初期化 |
| `workspace_agent/models.py` | リクエスト/レスポンスモデル |
| `workspace_agent/sdk_client.py` | Claude SDKラッパー |
| `workspace_agent/main.py` | コンテナ内FastAPI (UDS) |
| `app/services/container/__init__.py` | パッケージ初期化 |
| `app/services/container/models.py` | ContainerInfo, ContainerStatus |
| `app/services/container/config.py` | Docker API設定生成 |
| `app/services/container/lifecycle.py` | コンテナライフサイクル管理 |
| `app/services/container/orchestrator.py` | ContainerOrchestrator |
| `app/services/container/warm_pool.py` | WarmPoolManager |
| `app/services/container/gc.py` | ガベージコレクター |
| `app/services/proxy/__init__.py` | パッケージ初期化 |
| `app/services/proxy/domain_whitelist.py` | ドメインホワイトリスト |
| `app/services/proxy/sigv4.py` | AWS SigV4署名 |
| `app/services/proxy/credential_proxy.py` | CredentialInjectionProxy |
| `app/services/workspace/file_sync.py` | Container ↔ S3同期 |
| `app/services/aws_config.py` | AWSConfig (execute/から移動) |
| `docs/migration/03-security-verification.md` | セキュリティ検証レポート |

### 大幅改修

| ファイル | 変更内容 |
|---------|---------|
| `app/services/execute_service.py` | コンテナ隔離実行に全面書き換え |
| `app/main.py` | Orchestrator/GC初期化、AWS env var除去 |
| `app/api/conversations.py` | Orchestrator経由SSE中継 |
| `app/config.py` | コンテナ/プロキシ/WarmPool設定追加 |
| `docker-compose.yml` | Docker Socket/UDSボリュームマウント |
| `Dockerfile` | Docker Socket対応、ワークスペースSocket |
| `requirements.txt` | aiodocker追加 |

### 軽微な改修

| ファイル | 変更内容 |
|---------|---------|
| `app/api/health.py` | コンテナシステムヘルスチェック追加 |
| `app/services/bedrock_client.py` | AWSConfigインポートパス変更 |
| `app/services/simple_chat_service.py` | AWSConfigインポートパス変更 |

### 削除

| ファイル | 理由 |
|---------|------|
| `app/services/execute/` (ディレクトリ全体) | 旧in-process実行エンジン、コンテナ隔離により不要 |

---

## Phase 2: 運用品質 + セキュリティ強化

**ステータス**: Step 7/7 完了
**計画書**: `docs/migration/04-phase2-migration-plan.md`

### Phase 2 進捗サマリー

| Step | タスク | ステータス | 備考 |
|------|--------|-----------|------|
| 1 | WarmPool最適化 | :white_check_mark: 完了 | プリヒート、リトライ(exponential backoff)、メトリクス、ホットリロード |
| 2 | userns-remap | :white_check_mark: 完了 | daemon.json, subuid/subgid, ソケット権限調整, 検証・デプロイ手順書 |
| 3 | カスタムseccomp | :white_check_mark: 完了 | syscall分析, ホワイトリストプロファイル, コンテナ設定適用, 違反メトリクス |
| 4 | 監視ダッシュボード + アラート | :white_check_mark: 完了 | Prometheusメトリクス, Grafanaダッシュボード, 8種アラートルール |
| 5 | Proxyレイテンシ最適化 | :white_check_mark: 完了 | DNSキャッシュ, コネクションプール最適化, レイテンシ計測 |
| 6 | 統合テスト・検証 | :white_check_mark: 完了 | Phase 2統合テスト, セキュリティ検証レポート更新 |
| 7 | ドキュメント整備 | :white_check_mark: 完了 | 運用ガイド, セキュリティ設定ガイド, 進捗更新 |

### Phase 2 詳細進捗

#### Step 1: WarmPool最適化

- [x] 1.1 起動時プリヒート実装 → `app/services/container/warm_pool.py`, `app/main.py`
- [x] 1.2 補充リトライロジック（exponential backoff, 最大3回） → `warm_pool.py`
- [x] 1.3 プールメトリクス収集（枯渇回数、取得レイテンシ） → `warm_pool.py`
- [x] 1.4 プール枯渇アラート連携 → `monitoring/prometheus/alerts/workspace-alerts.yml`
- [x] 1.5 WarmPool設定ホットリロード（Redis経由） → `warm_pool.py`

#### Step 2: userns-remap

- [x] 2.1 subordinate UID/GIDマッピング定義 → `deployment/docker/subuid`, `subgid`
- [x] 2.2 Docker daemon設定ファイル → `deployment/docker/daemon.json`
- [x] 2.3 互換性検証手順書 → `docs/migration/05-userns-remap-verification.md`
- [x] 2.4 コンテナ設定のUsernsMode対応 → `app/services/container/config.py`
- [x] 2.5 ソケットファイル権限調整 → `app/services/container/lifecycle.py`
- [x] 2.6 デプロイメント手順書 → `docs/migration/06-userns-remap-deployment.md`

#### Step 3: カスタムseccomp

- [x] 3.1 必要syscall分析 → `docs/migration/07-seccomp-syscall-analysis.md`
- [x] 3.2 seccompプロファイルJSON → `deployment/seccomp/workspace-seccomp.json`
- [x] 3.3 プロファイル適用のコンテナ設定変更 → `app/services/container/config.py`
- [x] 3.4 設定パスの環境変数化 → `app/config.py` (`SECCOMP_PROFILE_PATH`)
- [x] 3.5 動作検証テスト → `tests/integration/test_phase2.py`
- [x] 3.6 seccomp違反メトリクス定義 → `app/infrastructure/metrics.py`

#### Step 4: 監視ダッシュボード + アラート

- [x] 4.1 Prometheusメトリクス定義追加 → `app/infrastructure/metrics.py`
- [x] 4.2 インフラメトリクス（コンテナ数、WarmPool、CPU/メモリ） → `metrics.py`
- [x] 4.3 アプリメトリクス（起動時間、成功率、クラッシュ、Proxyレイテンシ等） → `metrics.py`
- [x] 4.4 /metricsエンドポイント更新 → `app/main.py`
- [x] 4.5 計測ポイント埋め込み → `orchestrator.py`, `warm_pool.py`, `credential_proxy.py`, `gc.py`
- [x] 4.6 Grafanaダッシュボード → `monitoring/grafana/dashboards/workspace-containers.json`
- [x] 4.7 アラートルール（8種） → `monitoring/prometheus/alerts/workspace-alerts.yml`
- [x] 4.8 docker-compose監視スタック → `docker-compose.yml` (Prometheus + Grafana)

#### Step 5: Proxyレイテンシ最適化

- [x] 5.2 httpxコネクションプール最適化 → `app/services/proxy/credential_proxy.py`
- [x] 5.3 DNSキャッシュ導入 → `app/services/proxy/dns_cache.py`
- [x] 5.4 per-requestレイテンシ計測 → `credential_proxy.py`

#### Step 6: 統合テスト・検証

- [x] 6.1 Phase 2統合テスト → `tests/integration/test_phase2.py`
- [x] 6.2 セキュリティ検証レポート更新 → `docs/migration/03-security-verification.md`

#### Step 7: ドキュメント整備

- [x] 7.1 進捗管理ドキュメント更新 → `docs/migration/01-progress-tracker.md`
- [x] 7.2 監視運用ガイド → `docs/operations/monitoring-guide.md`
- [x] 7.3 セキュリティ設定ガイド → `docs/operations/security-config-guide.md`

---

### Phase 2 新規作成ファイル

| ファイル | 目的 |
|---------|------|
| `deployment/docker/daemon.json` | userns-remap Docker daemon設定 |
| `deployment/docker/subuid` | subordinate UIDマッピング |
| `deployment/docker/subgid` | subordinate GIDマッピング |
| `deployment/seccomp/workspace-seccomp.json` | カスタムseccompプロファイル |
| `app/services/proxy/dns_cache.py` | DNSキャッシュ |
| `monitoring/grafana/dashboards/workspace-containers.json` | Grafanaダッシュボード |
| `monitoring/prometheus/prometheus.yml` | Prometheus設定 |
| `monitoring/prometheus/alerts/workspace-alerts.yml` | アラートルール |
| `tests/integration/test_phase2.py` | Phase 2統合テスト |
| `docs/migration/04-phase2-migration-plan.md` | Phase 2移行計画書 |
| `docs/migration/05-userns-remap-verification.md` | userns-remap互換性検証 |
| `docs/migration/06-userns-remap-deployment.md` | userns-remapデプロイ手順 |
| `docs/migration/07-seccomp-syscall-analysis.md` | seccomp syscall分析 |
| `docs/operations/monitoring-guide.md` | 監視運用ガイド |
| `docs/operations/security-config-guide.md` | セキュリティ設定ガイド |

### Phase 2 改修ファイル

| ファイル | 変更内容 |
|---------|---------|
| `app/infrastructure/metrics.py` | ワークスペースコンテナメトリクス15種追加 |
| `app/services/container/warm_pool.py` | プリヒート、リトライ、メトリクス、ホットリロード |
| `app/services/container/config.py` | seccompプロファイル適用、UsernsMode対応 |
| `app/services/container/lifecycle.py` | userns-remapソケット権限調整 |
| `app/services/container/orchestrator.py` | コンテナ起動/リクエスト/クラッシュメトリクス |
| `app/services/container/gc.py` | GCサイクルメトリクス |
| `app/services/proxy/credential_proxy.py` | コネクションプール最適化、レイテンシ計測 |
| `app/config.py` | seccompパス、userns-remap設定追加 |
| `app/main.py` | WarmPoolプリヒート、/metricsにコンテナメトリクス追加 |
| `docker-compose.yml` | Prometheus + Grafana監視スタック |
| `docs/migration/01-progress-tracker.md` | Phase 2進捗追加 |
| `docs/migration/03-security-verification.md` | L2 seccomp + L7 userns検証追記 |

---

## 変更履歴

| 日時 | 内容 |
|------|------|
| 2026-02-07 | 移行計画書・進捗管理ドキュメント作成 |
| 2026-02-07 | Step 1-10 全ステップ実装完了 |
| 2026-02-07 | Phase 2 移行計画書策定 (`04-phase2-migration-plan.md`) |
| 2026-02-07 | Phase 2 全7ステップ実装完了 |
