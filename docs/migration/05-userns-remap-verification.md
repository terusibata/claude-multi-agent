# userns-remap 互換性検証手順書

**作成日**: 2026-02-07
**対象**: Phase 2 - Step 2 (userns-remap 導入)
**前提**: Phase 1 完了済み、Docker Engine 20.10+ 環境

---

## 1. 前提条件

| 項目 | 要件 | 確認コマンド |
|------|------|-------------|
| Docker Engine | 20.10 以上 | `docker version --format '{{.Server.Version}}'` |
| ストレージドライバ | overlay2 | `docker info --format '{{.Driver}}'` |
| OS | Linux (kernel 3.8+, user namespace対応) | `uname -r` |
| 権限 | root または sudo 権限 | `whoami` |

> **注意**: userns-remap はDocker daemonレベルの設定であるため、有効化にはDocker再起動が必要です。全ての実行中コンテナが停止されます。

---

## 2. 影響分析

### 2.1 影響範囲マトリクス

| 項目 | 影響 | 対応方法 | 作業量 |
|------|------|---------|--------|
| 既存コンテナ | 全て再作成が必要（Docker再起動により停止） | WarmPool再構築、ローリングアップデート | 高 |
| Bind mount | ホスト側ファイルのUID/GIDがリマップされる | ソケットディレクトリ(`/var/run/agent.sock`, `/var/run/proxy.sock`)の権限調整 | 中 |
| Docker volume | 自動的にリマップされる | 対応不要 | なし |
| ベースイメージ | 再ビルド不要（コンテナ内UIDはそのまま、ホスト上でリマップ） | 対応不要 | なし |
| tmpfs マウント | 影響なし（カーネルが管理） | 対応不要 | なし |

### 2.2 UIDマッピングの仕組み

```
コンテナ内 UID 0 (root)   → ホスト UID 100000 (dockremap)
コンテナ内 UID 1000 (appuser) → ホスト UID 101000
コンテナ内 UID N          → ホスト UID 100000 + N
```

これにより、コンテナ内でrootとして実行されているプロセスがコンテナエスケープした場合でも、ホスト上では非特権ユーザー(UID 100000)として扱われます。

---

## 3. 事前検証チェックリスト

### 3.1 環境確認

```bash
# 1. Dockerバージョン確認（20.10以上であること）
docker version --format '{{.Server.Version}}'

# 2. 現在のストレージドライバ確認（overlay2であること）
docker info --format '{{.Driver}}'

# 3. 現在のUser Namespace設定確認（無効であること）
docker info | grep "User Namespace"

# 4. カーネルのuser namespace対応確認
cat /proc/sys/kernel/unprivileged_userns_clone
# 1 であること（対応済み）

# 5. 実行中コンテナ一覧（全て影響を受ける）
docker ps --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"
```

### 3.2 設定ファイルバックアップ

```bash
# daemon.json のバックアップ
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak.$(date +%Y%m%d%H%M%S)

# subuid/subgid のバックアップ
sudo cp /etc/subuid /etc/subuid.bak.$(date +%Y%m%d%H%M%S)
sudo cp /etc/subgid /etc/subgid.bak.$(date +%Y%m%d%H%M%S)
```

### 3.3 確認項目チェックリスト

- [ ] Docker Engine 20.10+ であること
- [ ] ストレージドライバが overlay2 であること
- [ ] 実行中コンテナの一覧を記録したこと
- [ ] daemon.json のバックアップを取得したこと
- [ ] subuid/subgid のバックアップを取得したこと
- [ ] メンテナンスウィンドウが確保されていること
- [ ] ロードバランサーからのドレイン準備が完了していること

---

## 4. デプロイメント手順

### Step 1: dockremap ユーザー作成

```bash
# dockremap システムユーザーを作成（ホームディレクトリなし、ログイン不可）
sudo useradd -r -s /usr/sbin/nologin dockremap
```

### Step 2: subordinate UID/GID マッピング設定

```bash
# subuid/subgid ファイルを配置
sudo cp deployment/docker/subuid /etc/subuid
sudo cp deployment/docker/subgid /etc/subgid

# 設定内容確認
cat /etc/subuid
# 期待値: dockremap:100000:65536

cat /etc/subgid
# 期待値: dockremap:100000:65536
```

### Step 3: Docker daemon設定

```bash
# daemon.json を配置
sudo cp deployment/docker/daemon.json /etc/docker/daemon.json

# 設定内容確認
cat /etc/docker/daemon.json
# 期待値: {"userns-remap": "default", "storage-driver": "overlay2"} を含むこと
```

### Step 4: Docker再起動

```bash
# 全コンテナが停止することを再確認
docker ps -q | wc -l

# Docker再起動
sudo systemctl restart docker

# 再起動完了確認
sudo systemctl status docker
```

### Step 5: userns-remap 有効化確認

```bash
# User Namespaceの確認
docker info | grep "User Namespace"
# 期待値: "User Namespace: true" または "userns" を含む出力

# Docker Rootディレクトリの確認（リマップされたディレクトリが作成される）
ls -la /var/lib/docker/100000.100000/
# リマップ用のストレージディレクトリが存在すること
```

---

## 5. デプロイ後検証

### 5.1 コンテナ内UIDマッピング確認

```bash
# テストコンテナでUID確認
docker run --rm workspace-base:latest id
# 期待値: uid=1000(appuser) gid=1000(appuser) groups=1000(appuser)
# ※コンテナ内ではUID 1000のまま（ホスト上でリマップされる）

# ホスト側からコンテナプロセスのUIDを確認
docker run -d --name userns-test workspace-base:latest sleep 60
ps aux | grep "sleep 60"
# 期待値: UID 101000（100000 + 1000）で実行されていること
docker rm -f userns-test
```

### 5.2 ソケットファイル権限確認

```bash
# テストコンテナを起動してソケットの権限を確認
# ソケットディレクトリがリマップされたUIDでアクセス可能であること

# proxy.sock の権限確認
ls -la /tmp/workspace-sockets/
# ソケットファイルの所有者がリマップされたUID/GIDになっていること
```

### 5.3 ヘルスチェック実行

```bash
# アプリケーションのヘルスチェック
curl -s http://localhost:8000/health | python3 -m json.tool

# 確認項目:
# - docker: "healthy"
# - redis: "healthy"
# - container_system.warm_pool_size > 0（WarmPoolが再構築されていること）
```

### 5.4 ワークスペースコンテナの動作確認

```bash
# WarmPoolからコンテナを取得して動作検証
# 1. Python実行テスト
docker exec <container_id> python3 -c "print('userns-remap test: OK')"

# 2. ファイル書き込みテスト（/workspace）
docker exec <container_id> touch /workspace/test-userns
docker exec <container_id> ls -la /workspace/test-userns

# 3. pip install テスト
docker exec <container_id> pip install --user requests

# 4. git テスト
docker exec <container_id> git --version
```

### 5.5 デプロイ後チェックリスト

- [ ] `docker info` で User Namespace が有効であること
- [ ] テストコンテナが正常起動すること
- [ ] コンテナ内プロセスがホスト上でリマップされたUIDで動作すること
- [ ] ソケットファイルの権限が正しいこと
- [ ] ヘルスチェックが全項目 healthy であること
- [ ] WarmPool が正常にコンテナを生成できること
- [ ] Python/pip/git が正常動作すること
- [ ] Proxy経由の通信が正常であること

---

## 6. ロールバック手順

userns-remap に問題が発生した場合、以下の手順でロールバックします。

### 6.1 即時ロールバック

```bash
# 1. daemon.json から userns-remap を削除
sudo cp /etc/docker/daemon.json.bak.* /etc/docker/daemon.json
# または手動で userns-remap 行を削除:
# sudo vim /etc/docker/daemon.json
# "userns-remap" 行を削除

# 2. Docker再起動
sudo systemctl restart docker

# 3. 無効化確認
docker info | grep "User Namespace"
# 出力なし = 無効化完了

# 4. subuid/subgid を復元（任意）
sudo cp /etc/subuid.bak.* /etc/subuid
sudo cp /etc/subgid.bak.* /etc/subgid
```

### 6.2 ロールバック後の確認

```bash
# ヘルスチェック
curl -s http://localhost:8000/health | python3 -m json.tool

# WarmPool再構築確認
# アプリケーションが自動的にWarmPoolを再構築することを確認

# テストコンテナ起動
docker run --rm workspace-base:latest id
# uid=1000(appuser) で直接実行されること（リマップなし）
```

### 6.3 ロールバックトリガー条件

以下のいずれかに該当する場合、即座にロールバックを実施します:

| 条件 | 判定基準 |
|------|---------|
| コンテナ起動失敗 | WarmPoolのコンテナ作成が連続3回失敗 |
| ソケット通信障害 | Proxy経由の通信が全て失敗 |
| ヘルスチェック異常 | docker ステータスが unhealthy |
| パフォーマンス劣化 | コンテナ起動時間が P95 > 30秒（通常の3倍以上） |

---

## 7. 既知の注意事項

1. **ストレージ使用量**: userns-remap有効化後、`/var/lib/docker/100000.100000/` 配下に新しいストレージ領域が作成されます。既存のイメージは再pullが必要になる場合があります。

2. **--privileged コンテナ**: `--privileged` フラグを使用するコンテナはuserns-remapと互換性がありません。本プロジェクトでは使用していないため影響なし。

3. **ホストネットワークモード**: `--network host` を使用するコンテナは影響を受ける場合があります。本プロジェクトでは `--network none` を使用しているため影響なし。

4. **Docker API**: Docker APIへのアクセスは影響を受けません。コンテナ管理操作は従来通り動作します。
