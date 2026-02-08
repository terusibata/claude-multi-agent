# userns-remap デプロイメント手順書

**作成日**: 2026-02-07
**対象**: Phase 2 - Step 2.6 (userns-remap 本番デプロイ)
**前提**: `05-userns-remap-verification.md` の検証が完了していること

---

## 1. 概要

userns-remap の有効化はDocker daemonレベルの設定変更であり、**Docker再起動が必須**です。再起動中は当該ホスト上の全コンテナが停止するため、本番環境ではローリングアップデート方式で段階的にデプロイします。

### 影響範囲

| 影響 | 詳細 |
|------|------|
| ダウンタイム | ホスト単位でDocker再起動中（約30秒〜2分） |
| コンテナ | 当該ホスト上の全ワークスペースコンテナが停止・再作成 |
| WarmPool | 当該ホスト上のWarmPoolが空になり再構築が必要 |
| 進行中セッション | 当該ホスト上の進行中セッションは中断される |

---

## 2. メンテナンスウィンドウ計画

### 2.1 推奨時間帯

| 環境 | 推奨時間帯 | 理由 |
|------|-----------|------|
| 開発環境 | いつでも可 | 影響なし |
| ステージング | 業務時間外 | QAチームへの影響最小化 |
| 本番環境 | 深夜帯 (02:00-05:00 JST) | ユーザートラフィック最小 |

### 2.2 所要時間見積もり

| 作業 | 所要時間（ホストあたり） |
|------|------------------------|
| ロードバランサーからのドレイン | 1分 |
| コンテナのグレースフルシャットダウン | 2-5分 |
| userns-remap設定適用 | 1分 |
| Docker再起動 | 30秒-2分 |
| WarmPool再構築 | 3-5分 |
| 動作検証 | 3-5分 |
| ロードバランサーへの復帰 | 1分 |
| **合計（ホストあたり）** | **約12-20分** |

### 2.3 マルチホスト環境での総所要時間

```
総所要時間 = ホストあたり所要時間 × ホスト数 + バッファ（ホストあたり5分）
```

例: 3ホスト環境の場合 → 約60-75分

---

## 3. 事前準備

### 3.1 設定ファイルの準備

以下のファイルが全対象ホストに配布可能であることを確認します:

```bash
# リポジトリ内の設定ファイル確認
ls -la deployment/docker/subuid
ls -la deployment/docker/subgid
ls -la deployment/docker/daemon.json
```

### 3.2 事前確認チェックリスト

- [ ] 全対象ホストで `05-userns-remap-verification.md` の事前検証チェックリストが完了
- [ ] ロールバック手順を全オペレーターが確認済み
- [ ] 監視ダッシュボードへのアクセスが可能
- [ ] 関係者への事前通知が完了
- [ ] メンテナンスウィンドウが承認済み
- [ ] バックアップ（daemon.json, subuid, subgid）が全ホストで取得済み

---

## 4. ローリングアップデート手順

### 4.1 手順概要

```
Host 1                 Host 2                 Host 3
  │                      │                      │
  ▼                      │                      │
[ドレイン]               │                      │
[シャットダウン]          │                      │
[設定適用]               │                      │
[Docker再起動]           │                      │
[検証]                   │                      │
[復帰] ─── 確認OK ───→  ▼                      │
                       [ドレイン]               │
                       [シャットダウン]          │
                       [設定適用]               │
                       [Docker再起動]           │
                       [検証]                   │
                       [復帰] ─── 確認OK ───→  ▼
                                              [ドレイン]
                                              [シャットダウン]
                                              [設定適用]
                                              [Docker再起動]
                                              [検証]
                                              [復帰]
                                                │
                                                ▼
                                             完了
```

> **重要**: 各ホストの検証が完了し、正常動作を確認してから次のホストに進みます。問題が発生した場合は即座にロールバックし、後続ホストの作業を中止します。

### 4.2 ホストごとの詳細手順

以下の手順を対象ホストごとに順番に実行します。

#### Step 1: ロードバランサーからホストをドレイン

```bash
# ロードバランサーから対象ホストを除外
# （環境に応じたLB操作コマンド）

# 新規リクエストが対象ホストにルーティングされないことを確認
# ※既存セッションのドレインを待つ（最大2分）
sleep 120
```

#### Step 2: ワークスペースコンテナのグレースフルシャットダウン

```bash
# 実行中のワークスペースコンテナ一覧を記録
docker ps --filter "label=workspace" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}" | tee /tmp/pre-deploy-containers.txt

# アプリケーション経由でグレースフルシャットダウン
# （ContainerOrchestratorのshutdown APIを呼び出す）
curl -X POST http://localhost:8000/api/admin/drain

# 全コンテナが停止するまで待機（最大5分）
timeout=300
elapsed=0
while [ "$(docker ps --filter 'label=workspace' -q | wc -l)" -gt 0 ] && [ $elapsed -lt $timeout ]; do
    echo "待機中... $(docker ps --filter 'label=workspace' -q | wc -l) コンテナ残存"
    sleep 10
    elapsed=$((elapsed + 10))
done

# 残存コンテナの強制停止（タイムアウト時）
docker ps --filter "label=workspace" -q | xargs -r docker stop --time 10
```

#### Step 3: userns-remap 設定適用

```bash
# dockremap ユーザー作成（未作成の場合）
id dockremap &>/dev/null || sudo useradd -r -s /usr/sbin/nologin dockremap

# subuid/subgid 配置
sudo cp deployment/docker/subuid /etc/subuid
sudo cp deployment/docker/subgid /etc/subgid

# daemon.json 配置
sudo cp deployment/docker/daemon.json /etc/docker/daemon.json

# 設定内容の最終確認
echo "=== /etc/subuid ==="
cat /etc/subuid
echo "=== /etc/subgid ==="
cat /etc/subgid
echo "=== /etc/docker/daemon.json ==="
cat /etc/docker/daemon.json
```

#### Step 4: Docker daemon再起動

```bash
# Docker再起動
sudo systemctl restart docker

# 起動完了を待機
timeout=60
elapsed=0
while ! docker info &>/dev/null && [ $elapsed -lt $timeout ]; do
    echo "Docker起動待ち... ${elapsed}秒"
    sleep 5
    elapsed=$((elapsed + 5))
done

# 起動確認
if docker info &>/dev/null; then
    echo "Docker再起動完了"
else
    echo "ERROR: Docker起動タイムアウト - ロールバックを検討してください"
    exit 1
fi

# userns-remap有効化確認
docker info | grep -i "user"
docker info | grep -i "namespace"
```

#### Step 5: ワークスペースコンテナの動作検証

```bash
# 1. テストコンテナ起動
docker run --rm workspace-base:latest id
# 期待値: uid=1000(appuser)

# 2. ホスト側UIDマッピング確認
docker run -d --name userns-verify workspace-base:latest sleep 30
CONTAINER_PID=$(docker inspect --format '{{.State.Pid}}' userns-verify)
echo "コンテナPID: $CONTAINER_PID"
cat /proc/$CONTAINER_PID/uid_map
# 期待値: 0 100000 65536 のようなマッピング
docker rm -f userns-verify

# 3. アプリケーション起動（WarmPool再構築開始）
# ※アプリケーション管理方法に応じて実行
sudo systemctl restart workspace-app  # 例

# 4. WarmPool再構築待機
echo "WarmPool再構築を待機中..."
sleep 30

# 5. ヘルスチェック
curl -s http://localhost:8000/health | python3 -m json.tool

# 6. ワークスペース機能テスト
# テストリクエストを送信して、コンテナ作成〜コード実行の一連の流れを確認
curl -s -X POST http://localhost:8000/api/test/workspace-verify
```

#### Step 6: ロードバランサーにホストを復帰

```bash
# ヘルスチェックが全項目OKであることを確認後、LBに復帰
# （環境に応じたLB操作コマンド）

# トラフィックが正常にルーティングされていることを確認
# 5分間監視してエラーがないことを確認
echo "5分間の監視を開始..."
for i in $(seq 1 30); do
    STATUS=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health)
    echo "$(date): ヘルスチェック HTTP $STATUS"
    sleep 10
done
```

---

## 5. ロールアウト中の監視チェックリスト

各ホストのデプロイ完了後、次のホストに進む前に以下を確認します:

### 5.1 即時確認（デプロイ直後）

- [ ] `docker info` で userns-remap が有効であること
- [ ] Docker daemon が正常起動していること (`systemctl status docker`)
- [ ] テストコンテナが正常に起動・実行できること
- [ ] ヘルスチェック API が正常応答すること

### 5.2 短期確認（デプロイ後5分）

- [ ] WarmPool が再構築されていること（ヘルスチェックで warm_pool_size > 0）
- [ ] 新規ワークスペースリクエストが正常に処理されること
- [ ] Proxy経由の通信が正常であること
- [ ] エラーログに異常なエントリがないこと

### 5.3 継続監視（デプロイ後30分）

- [ ] コンテナ起動時間が SLO 内であること（P95 < 10秒）
- [ ] リクエスト成功率が SLO 内であること（> 95%）
- [ ] クラッシュ率が SLO 内であること（< 5%）
- [ ] Proxy レイテンシが SLO 内であること（P95 < 100ms）
- [ ] S3同期が正常に動作していること

### 5.4 監視ダッシュボード確認項目

| パネル | 確認内容 | 正常値 |
|--------|---------|--------|
| Container Lifecycle | コンテナ起動成功率 | > 95% |
| Container Lifecycle | コンテナ起動時間 P95 | < 10秒 |
| WarmPool | プールサイズ | min_size 以上 |
| WarmPool | 枯渇回数 | 0 |
| Proxy | リクエスト成功率 | > 95% |
| Proxy | レイテンシ P95 | < 100ms |
| Security & GC | seccomp違反 | 0（増加なし） |
| Security & GC | GC成功率 | 100% |

---

## 6. ロールバックトリガー条件

以下のいずれかに該当する場合、**即座にロールバック**を実施し、後続ホストの作業を中止します。

### 6.1 即時ロールバック（自動判定）

| 条件 | 判定基準 | 検出方法 |
|------|---------|---------|
| Docker起動失敗 | systemctl restart docker が失敗 | コマンド終了コード |
| コンテナ起動不能 | テストコンテナが起動しない | docker run 失敗 |
| ヘルスチェック異常 | /health が 500 を返す | curl レスポンスコード |
| ソケット通信障害 | Proxy経由通信が全て失敗 | ワークスペーステスト失敗 |

### 6.2 判断ロールバック（手動判定）

| 条件 | 判定基準 | 判断者 |
|------|---------|--------|
| パフォーマンス劣化 | コンテナ起動P95 > 30秒が5分間継続 | オペレーター |
| エラー率上昇 | リクエスト成功率 < 80% が5分間継続 | オペレーター |
| 予期しないエラー | 未知のエラーパターンが多発 | オペレーター |

### 6.3 ロールバック手順

```bash
# 1. ロードバランサーからドレイン（復帰済みの場合）
# （環境に応じたLB操作コマンド）

# 2. daemon.json からuserns-remapを除去
sudo cp /etc/docker/daemon.json.bak.* /etc/docker/daemon.json

# 3. subuid/subgid を復元
sudo cp /etc/subuid.bak.* /etc/subuid
sudo cp /etc/subgid.bak.* /etc/subgid

# 4. Docker再起動
sudo systemctl restart docker

# 5. 無効化確認
docker info | grep "User Namespace"
# 出力なし = ロールバック完了

# 6. アプリケーション再起動
sudo systemctl restart workspace-app

# 7. ヘルスチェック確認
curl -s http://localhost:8000/health | python3 -m json.tool

# 8. ロードバランサーに復帰
# （環境に応じたLB操作コマンド）
```

---

## 7. デプロイ完了後の作業

### 7.1 全ホスト完了後の確認

- [ ] 全ホストで userns-remap が有効であること
- [ ] 全ホストのヘルスチェックが正常であること
- [ ] 全ホストのWarmPoolが正常サイズであること
- [ ] 全SLI/SLOが基準値内であること
- [ ] エラーログに異常がないこと

### 7.2 ドキュメント更新

- [ ] `docs/migration/01-progress-tracker.md` に完了記録を追加
- [ ] `docs/migration/03-security-verification.md` のL7ステータスを更新
- [ ] 運用チームへの完了通知

### 7.3 事後監視

| 期間 | 監視頻度 | 確認内容 |
|------|---------|---------|
| デプロイ後24時間 | 1時間ごと | SLI/SLO、エラーログ |
| デプロイ後1週間 | 1日1回 | 長期トレンド、パフォーマンス変化 |
| デプロイ後1ヶ月 | 週1回 | 安定性確認、最終報告 |
