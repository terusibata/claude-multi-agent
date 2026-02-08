# Phase 5: 残作業完了・本番準備 移行計画書

## 概要

Phase 1〜4 で、コンテナ隔離アーキテクチャの中核機能（コンテナ分離・WarmPool・セキュリティ基盤・SDK統合・通信経路）は実装済み。
Phase 5 では、実装仕様書に記載されながら未実装の項目、および本番運用に必要な品質基準を満たすための残作業を体系的に完了する。

### Phase 5 の方針

- **仕様書記載の未実装項目を優先的に完了**する
- **E2E動作確認**を最終ステップとして必ず実施する
- セキュリティ強化（Phase 3 ロードマップ）の残項目を完了する
- 運用品質（障害復旧・監視・ログ）の仕上げを行う

---

## 現状分析: 仕様書 vs 実装ギャップ

### 実装完了（Phase 1〜4）

| 項目 | ステータス |
|------|-----------|
| コンテナ隔離 (`--network none` + seccomp + cgroups) | ✅ 完了 |
| Credential Injection Proxy (SigV4注入 + ドメインWL) | ✅ 完了 |
| Container Orchestrator (aiodocker + Redis) | ✅ 完了 |
| WarmPool (Redis, プリヒート, ホットリロード) | ✅ 完了 |
| GC (TTL + 孤立コンテナ回収) | ✅ 完了 |
| S3ファイル同期 (双方向) | ✅ 完了 |
| workspace-base イメージ (socat + エントリポイント) | ✅ 完了 |
| workspace_agent (SDK統合 + SSE + MCP) | ✅ 完了 |
| Execute Service (コンテナ隔離実行) | ✅ 完了 |
| userns-remap 設定 | ✅ 完了 |
| カスタム seccomp プロファイル | ✅ 完了 |
| Prometheus メトリクス + Grafana ダッシュボード + アラート | ✅ 完了 |
| グレースフルシャットダウン | ✅ 完了 |
| 構造化ログ (JSON + リクエストID) | ✅ 完了 |
| Proxyレイテンシ最適化 (DNSキャッシュ) | ✅ 完了 |
| SDK API準拠 (ClaudeAgentOptions, Message型変換) | ✅ 完了 |
| TCP→UDS リバースプロキシ (socat) | ✅ 完了 |

### 未実装・部分実装

| 項目 | ステータス | 仕様書セクション |
|------|-----------|-----------------|
| AppArmor プロファイル | ❌ 未実装 | セキュリティ（多層防御）/ 実装ロードマップ Phase 3 |
| セキュリティ監査ログ集約 | ⚠️ 部分実装 | 実装ロードマップ Phase 3 |
| クラッシュ復旧フロー（ユーザー通知） | ⚠️ 部分実装 | 障害復旧 |
| S3ライフサイクルポリシー | ❌ 未実装 | S3ライフサイクル |
| 定期的なファイル同期（ツール結果ごと） | ❌ 未実装 | ファイル同期 / 定期同期 |
| Node.js Proxy設定（global-agent / NODE_USE_ENV_PROXY） | ❌ 未実装 | ネットワーク隔離 |
| E2E統合テスト | ⚠️ 部分実装 | Phase 4 ステップ8 |
| ペネトレーションテスト（セキュリティ検証） | ❌ 未実装 | 実装ロードマップ Phase 3 |

---

## 実装計画

### ステップ 1: AppArmor プロファイル作成・適用

**目標**: 仕様書記載のセキュリティ多層防御 L2 の AppArmor 層を完成させる

**背景**: 現在は seccomp（syscall制御）のみ。AppArmor はファイルパス単位のアクセス制御を提供し、seccompと補完関係にある。

**タスク**:

1-1. AppArmor プロファイルの作成
  - `/workspace/**` と `/opt/venv/**` に rw 許可
  - `/proc/*/mem` と `/sys/**` を deny
  - `/etc/passwd`, `/etc/shadow` を deny
  - プロファイル配置先: `deployment/apparmor/workspace-container`

1-2. コンテナ作成設定に AppArmor プロファイル適用を追加
  - `app/services/container/config.py` の `SecurityOpt` に `apparmor=workspace-container` を追加
  - `app/config.py` に `apparmor_profile_name` 設定を追加（デフォルト: `workspace-container`）

1-3. AppArmor プロファイルのロード手順書作成
  - ホスト側での `apparmor_parser -r` コマンドの実行手順
  - docker-compose 起動前の前提条件として記述

**検証**:
- コンテナ内から `/proc/self/mem` への書き込みが denied されること
- `/workspace/` 以下でファイル読み書きが正常に動作すること
- `/sys/` 以下の書き込みが denied されること

---

### ステップ 2: セキュリティ監査ログの構造化・集約

**目標**: Proxy の通信ログとコンテナ操作ログを統一的な監査ログフォーマットで出力し、集約可能にする

**タスク**:

2-1. 監査ログイベントの定義
  - 仕様書のログフォーマットに準拠した構造化イベント:
    - `container_created`, `container_destroyed`, `container_crashed`
    - `proxy_request_allowed`, `proxy_request_blocked`
    - `file_sync_to_container`, `file_sync_from_container`
    - `agent_execution_started`, `agent_execution_completed`, `agent_execution_failed`
  - 各イベントに `conversation_id`, `tenant_id`, `container_id` を必ず含める

2-2. 監査ログ出力の統一
  - `app/services/container/orchestrator.py`: コンテナ操作イベント
  - `app/services/proxy/credential_proxy.py`: Proxy通信イベント
  - `app/services/workspace/file_sync.py`: ファイル同期イベント
  - `app/services/execute_service.py`: エージェント実行イベント
  - 全ログに仕様書記載の JSON フォーマットを適用

2-3. 構造化ログに `conversation_id` / `container_id` をコンテキスト変数として自動付与
  - orchestrator.execute() 内で structlog のコンテキストにバインドし、
    以降の呼び出しチェーンで自動的にログに含まれるようにする

**検証**:
- 1回のエージェント実行サイクルで、全ライフサイクルイベントが出力されること
- 各ログ行が JSON として valid で、必須フィールドが欠損していないこと

---

### ステップ 3: クラッシュ復旧フロー完成

**目標**: 仕様書記載のクラッシュ復旧フローを完全実装し、ユーザーに復旧状態を通知する

**現状**: コンテナ不健全検知 → 新コンテナ割当 → S3復元は実装済み。ユーザーへの通知SSEイベントが未実装。

**タスク**:

3-1. `container_lost` SSEイベントの定義と送出
  - orchestrator 内でコンテナ不健全検知時に `container_lost` イベントを発行
  - execute_service で受信し、クライアントに `event: container_recovered` SSEイベントとして通知
  - イベントデータ: `{"message": "Container recovered", "recovered": true}`

3-2. 実行中のコンテナクラッシュ時のリカバリフロー
  - execute() 内の Exception ハンドリングで、コンテナクラッシュ（接続エラー等）を検知
  - 新コンテナを自動割当し、S3からファイルを復元
  - `event: error` に加え `event: container_recovered` を送信

3-3. Proxy クラッシュ時の自動再起動
  - orchestrator._start_proxy() を再呼び出しするリカバリパスを追加
  - execute() でProxy接続エラーを検知した場合にProxy再起動を試行

**検証**:
- コンテナ強制停止後に新リクエストが新コンテナで処理されること
- クライアントに復旧通知SSEイベントが送信されること

---

### ステップ 4: S3 ライフサイクルポリシー定義

**目標**: 仕様書記載のS3ライフサイクルルールを IaC (Infrastructure as Code) として定義する

**タスク**:

4-1. S3 ライフサイクルポリシーの定義ファイル作成
  - `deployment/s3/lifecycle-policy.json` に以下のルールを記述:
    - 非最新バージョン 30日経過 → 削除
    - 90日アクセスなし → Glacier 移行
    - archived 後 180日 → 削除
  - S3バケットバージョニングの有効化も併せて記述

4-2. ライフサイクル適用スクリプト作成
  - `deployment/s3/apply-lifecycle.sh`: AWS CLI で S3 バケットにポリシーを適用
  - `deployment/s3/README.md`: 適用手順ドキュメント

**検証**:
- `aws s3api get-bucket-lifecycle-configuration` でポリシーが正しく設定されていること

---

### ステップ 5: 実行中のファイル定期同期

**目標**: 仕様書記載の「実行中はツール結果ごとに差分をS3同期（非同期、データ損失最小化）」を実装する

**タスク**:

5-1. SSEイベントストリーム内での `tool_result` イベント検知
  - execute_service の SSE 中継処理内で `tool_result` イベントを検知
  - ファイル操作系ツール（write_file, create_file 等）の結果を特定

5-2. 非同期ファイル同期のトリガー
  - `tool_result` 検出時に `asyncio.create_task()` でバックグラウンド同期を実行
  - WorkspaceFileSync.sync_from_container() を非同期呼び出し
  - タスク参照を保持し、例外をログ出力する（WarmPoolと同じパターン）

5-3. 同期の重複防止
  - 同一コンテナへの並行同期を防ぐロック機構を追加
  - 直近N秒以内に同期済みなら、次のtool_resultではスキップ（デバウンス）

**検証**:
- エージェントがファイル生成ツールを実行した直後にS3同期が開始されること
- 長時間実行中にコンテナがクラッシュしても、直近の同期までのファイルが保持されること

---

### ステップ 6: Node.js Proxy 設定対応

**目標**: コンテナ内の Node.js プロセス（Claude Code CLI）が `HTTP_PROXY` を正しく利用できるようにする

**背景**: Node.js 20 は `HTTP_PROXY` を自動的に fetch() に適用しない。`global-agent` パッケージまたは `NODE_USE_ENV_PROXY=1`（Node.js 24+）が必要。

**タスク**:

6-1. `global-agent` npm パッケージのインストール追加
  - `workspace-base/Dockerfile` に `npm install -g global-agent` を追加

6-2. コンテナ環境変数に `GLOBAL_AGENT_HTTP_PROXY` を追加
  - `app/services/container/config.py` の `Env` に以下を追加:
    - `GLOBAL_AGENT_HTTP_PROXY=http://127.0.0.1:8080`
    - `GLOBAL_AGENT_HTTPS_PROXY=http://127.0.0.1:8080`
    - `GLOBAL_AGENT_NO_PROXY=localhost,127.0.0.1`

6-3. エントリポイントで `global-agent` のブートストラップ
  - `NODE_OPTIONS=--require global-agent/bootstrap` を環境変数に追加
  - または entrypoint.sh で `export NODE_OPTIONS="--require global-agent/bootstrap"`

**検証**:
- コンテナ内で `node -e "fetch('https://api.anthropic.com').then(r => console.log(r.status))"` がProxy経由で通信できること
- Claude Code CLI がProxy経由でAPI呼び出しできること

---

### ステップ 7: E2E 統合テスト整備

**目標**: docker-compose up → エージェント実行 → レスポンス返却 の一連のフローを自動テストで検証できる状態にする

**タスク**:

7-1. テスト環境セットアップスクリプト
  - `tests/e2e/setup.sh`: workspace-base イメージビルド + docker-compose up
  - テスト終了後のクリーンアップ

7-2. コンテナライフサイクル E2E テスト
  - WarmPool プリヒートでコンテナが min_size 分作成されること
  - `orchestrator.get_or_create()` でコンテナが取得できること
  - Unix Socket 経由で `/execute` にリクエストが到達すること
  - GC が非アクティブコンテナを正しく破棄すること
  - `destroy_all()` で全リソース（コンテナ + Proxy）がクリーンアップされること

7-3. Proxy 通信テスト
  - HTTP 平文リクエストが Proxy を通過すること
  - HTTPS CONNECT リクエストが Proxy を通過すること
  - ドメインホワイトリスト外のリクエストが 403 で拒否されること
  - Bedrock API へのリクエストに SigV4 ヘッダーが注入されること

7-4. セキュリティ制約テスト
  - `--network none` でコンテナから外部IPに直接通信できないこと
  - `ReadonlyRootfs` で `/` への書き込みが失敗すること
  - `PidsLimit` で fork bomb が制限されること
  - seccomp プロファイルが適用されていること

7-5. SSE ストリーミング E2E テスト
  - workspace_agent → execute_service → API のイベント変換チェーンが正常に動作すること
  - `text_delta`, `tool_use`, `tool_result`, `result` の各イベント型が正しく中継されること
  - エラー時に `event: error` が返却されること

7-6. ファイル同期 E2E テスト
  - S3 → コンテナへのファイル同期（コンテナ起動時）が動作すること
  - コンテナ → S3 へのファイル同期（実行完了時）が動作すること
  - S3 未設定環境でエラーなく動作すること

**検証**:
- `pytest tests/e2e/ -v` で全テストがパスすること

---

### ステップ 8: ペネトレーションテスト実施・結果文書化

**目標**: 仕様書の実装ロードマップ Phase 3 に記載されたペネトレーションテストを実施し、結果を文書化する

**タスク**:

8-1. テストシナリオ定義
  - 仕様書「破壊的コマンド耐性」の全項目を検証シナリオとして定義:
    - `rm -rf /` → read-only rootfs で阻止されること
    - fork bomb (`:(){:|:&};:`) → pids_limit で制限されること
    - `dd if=/dev/zero of=/workspace/fill bs=1M` → disk quota で制限されること
    - メモリ枯渇 (`python -c "a=[' '*10**9]*10"`) → OOM Killer で処理されること
    - CPU独占（無限ループ）→ cpu_quota で制限されること
    - `curl 169.254.169.254` → `--network none` で阻止されること
    - `env | grep AWS` → 認証情報が環境変数に存在しないこと
    - 悪意パッケージインストール → ドメインホワイトリストで制限されること
    - 権限昇格 → seccomp + no-new-privileges で阻止されること

8-2. テスト実施と結果記録
  - 各シナリオの実行結果を `docs/security/penetration-test-report.md` に記録
  - 発見事項と対応状況を明記

8-3. セキュリティ検証レポート更新
  - `docs/migration/03-security-verification.md` にペネトレーションテスト結果を追記

**検証**:
- 全シナリオが期待通りに防御されること
- 検証結果が文書化されていること

---

### ステップ 9: ドキュメント整備・進捗管理更新

**目標**: 全移行ドキュメントを最新状態に更新し、Phase 5 完了を記録する

**タスク**:

9-1. `docs/migration/01-progress-tracker.md` に Phase 5 進捗を追加

9-2. Phase 5 完了基準の最終確認チェック

9-3. 本番デプロイ向けの残課題リスト作成
  - 本番デプロイ時に別途対応が必要な項目（マルチホスト、ASG等）を整理

---

## 実装順序と依存関係

```
ステップ 1 (AppArmor)       ステップ 4 (S3ライフサイクル)
    │                            │
    ▼                            ▼
ステップ 2 (監査ログ)       ステップ 6 (Node.js Proxy)
    │                            │
    ▼                            │
ステップ 3 (クラッシュ復旧)      │
    │                            │
    ├────────────────────────────┘
    ▼
ステップ 5 (定期ファイル同期)
    │
    ▼
ステップ 7 (E2E統合テスト)
    │
    ▼
ステップ 8 (ペネトレーションテスト)
    │
    ▼
ステップ 9 (ドキュメント整備)
```

**並行可能**: ステップ 1 と 4, ステップ 2 と 6
**クリティカルパス**: ステップ 1 → 2 → 3 → 5 → 7 → 8 → 9

---

## 修正対象ファイル一覧

| ファイル | ステップ | 内容 |
|---------|---------|------|
| `deployment/apparmor/workspace-container` | 1 | AppArmor プロファイル（新規） |
| `app/services/container/config.py` | 1, 6 | AppArmor 適用 / Node.js Proxy 環境変数 |
| `app/config.py` | 1 | AppArmor プロファイル名設定 |
| `app/services/container/orchestrator.py` | 2, 3 | 監査ログ / クラッシュ復旧 |
| `app/services/proxy/credential_proxy.py` | 2 | 監査ログイベント統一 |
| `app/services/workspace/file_sync.py` | 2, 5 | 監査ログ / 定期同期 |
| `app/services/execute_service.py` | 2, 3, 5 | 監査ログ / クラッシュ復旧 / 定期同期 |
| `deployment/s3/lifecycle-policy.json` | 4 | S3 ライフサイクル（新規） |
| `deployment/s3/apply-lifecycle.sh` | 4 | 適用スクリプト（新規） |
| `workspace-base/Dockerfile` | 6 | global-agent インストール |
| `workspace-base/entrypoint.sh` | 6 | NODE_OPTIONS 設定 |
| `tests/e2e/` | 7 | E2E テスト（新規ディレクトリ） |
| `docs/security/penetration-test-report.md` | 8 | ペネトレーションテスト結果（新規） |
| `docs/migration/01-progress-tracker.md` | 9 | 進捗更新 |
| `docs/migration/03-security-verification.md` | 8 | セキュリティ検証更新 |

---

## 完了基準

- [x] AppArmor プロファイルが作成され、コンテナに適用されている
- [x] 全ライフサイクルイベントが統一フォーマットの監査ログとして出力される
- [x] コンテナクラッシュ時にユーザーへ復旧通知SSEイベントが送信される
- [x] Proxy クラッシュ時に自動再起動が動作する
- [x] S3 ライフサイクルポリシーが定義され、適用手順が文書化されている
- [x] エージェント実行中にツール結果ごとのファイル同期が動作する
- [x] Node.js プロセスが Proxy 経由で外部通信できる
- [x] E2E 統合テストが全てパスする
- [x] 全ペネトレーションテストシナリオが期待通りに防御される
- [x] 移行ドキュメントが最新状態に更新されている

---

## リスクと注意事項

1. **ステップ 1（AppArmor）**: ホストカーネルに AppArmor が有効でない場合は適用不可。Ubuntu ではデフォルト有効だが、Amazon Linux 2 等では SELinux が優先される場合がある。ホスト OS の確認が必須。

2. **ステップ 5（定期ファイル同期）**: SSEストリーム中継中にバックグラウンド同期を行うため、性能影響に注意。デバウンス間隔の適切なチューニングが必要。

3. **ステップ 6（global-agent）**: Node.js 20 での `global-agent` の互換性を事前検証すること。一部ライブラリは `global-agent` のHookを無視する場合がある。

4. **ステップ 7（E2E テスト）**: Docker デーモンが利用可能な環境でのみ実行可能。CI/CD では Docker-in-Docker または専用テストランナーの準備が必要。

5. **ステップ 8（ペネトレーションテスト）**: 実コンテナ上でのテストとなるため、テスト環境の準備が前提。本番環境では実施しないこと。
