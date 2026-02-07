# 04 - セキュリティ設計

## 多層防御アーキテクチャ

```
Layer 1: Docker コンテナ隔離 (namespace, cgroups)
Layer 2: Linux Security Modules (seccomp, AppArmor)
Layer 3: リソース制限 (CPU, Memory, Disk, PIDs)
Layer 4: ネットワーク隔離 (iptables, Docker network)
Layer 5: ファイルシステム制限 (read-only root, tmpfs)
Layer 6: Claude SDK サンドボックス (bubblewrap)
```

## リソース制限

### コンテナリソース設定

```python
CONTAINER_RESOURCE_LIMITS = {
    # CPU
    "cpu_period": 100000,
    "cpu_quota": 200000,      # 2 CPU cores
    "cpuset_cpus": None,      # 動的割り当て

    # メモリ
    "mem_limit": "4g",         # 4GB ハードリミット
    "memswap_limit": "4g",    # swap 無効化（mem_limit と同値）

    # プロセス数（フォークボム対策）
    "pids_limit": 256,

    # ディスク（overlay2 + XFS pquota が必要）
    "storage_opt": {"size": "5G"},
}
```

### 各制限の理由

| 制限 | 値 | 脅威 | 効果 |
|------|-----|------|------|
| CPU 2 cores | `cpu_quota=200000` | 暗号マイニング、無限ループ | CPU 独占を防止 |
| Memory 4GB | `mem_limit=4g` | メモリリーク、大量データ処理 | OOM Killer が発動 |
| Swap 無効 | `memswap_limit=4g` | swap 使用によるホスト劣化 | メモリ超過で即停止 |
| PIDs 256 | `pids_limit=256` | フォークボム `:(){ :\|:& };:` | プロセス数を制限 |
| Disk 5GB | `storage_opt size=5G` | 大量ファイル生成、core dump | ディスク消費を制限 |

## ネットワーク隔離

### Docker ネットワーク構成

```
┌─ Host ──────────────────────────────────────────┐
│                                                   │
│  ┌─ backend-network (bridge) ──────────────────┐ │
│  │  Backend, PostgreSQL, Redis                  │ │
│  └──────────────────────────────────────────────┘ │
│                                                   │
│  ┌─ workspace-network (bridge, internal) ──────┐ │
│  │  Workspace Containers                        │ │
│  │  ※ backend-network へのアクセス不可          │ │
│  │  ※ インターネットアクセス: 制限付き許可      │ │
│  └──────────────────────────────────────────────┘ │
│                                                   │
│  Backend は両ネットワークに接続                    │
└───────────────────────────────────────────────────┘
```

### iptables ルール

```bash
# Workspace コンテナから内部サービスへのアクセスをブロック
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 172.18.0.0/16 -j DROP

# メタデータエンドポイントをブロック（AWS EC2 環境）
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 169.254.169.254 -j DROP

# プライベートネットワークへのアクセスをブロック
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 10.0.0.0/8 -j DROP
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 192.168.0.0/16 -j DROP

# 確立済み接続は許可（レスポンス受信用）
iptables -I DOCKER-USER -m state --state RELATED,ESTABLISHED -j ACCEPT

# Backend → Workspace コンテナ間の通信は許可（内部 API 用）
# ※ Backend が workspace-network にも接続しているため自動的に許可
```

### インターネットアクセス

- `pip install` のために PyPI へのアクセスは許可
- 許可ドメインのホワイトリスト:
  - `pypi.org`, `files.pythonhosted.org` (pip)
  - `registry.npmjs.org` (npm)
  - `api.anthropic.com` (Claude API)
  - 各テナントの Bedrock エンドポイント
- その他のアウトバウンド通信は原則ブロック（段階的に緩和可能）

## ファイルシステム保護

### Read-Only Root + Writable マウント

```python
CONTAINER_SECURITY_OPTIONS = {
    "read_only": True,  # ルートファイルシステムを読み取り専用に
    "tmpfs": {
        "/tmp": "size=1G,noexec",     # 一時ファイル用
        "/var/tmp": "size=512M",       # 追加一時領域
        "/run": "size=64M",           # ランタイム用
    },
    # /workspace と /opt/venv は書き込み可能ボリューム
}
```

### ディレクトリ権限

| パス | 権限 | 説明 |
|------|------|------|
| `/` (root) | read-only | システムファイル保護 |
| `/workspace` | read-write | ユーザーファイル領域 |
| `/opt/venv` | read-write | pip install 先 |
| `/tmp` | read-write, noexec | 一時ファイル（実行不可） |
| `/home/appuser` | read-write | ホームディレクトリ |

## コンテナセキュリティオプション

### Docker Run 相当の設定

```python
SECURITY_CONFIG = {
    # Capability をすべてドロップし、必要なものだけ追加
    "cap_drop": ["ALL"],
    "cap_add": ["CHOWN", "SETUID", "SETGID"],  # venv 操作に必要

    # 新しい特権の取得を禁止
    "security_opt": [
        "no-new-privileges:true",
        # "seccomp=workspace-seccomp.json",  # カスタム seccomp プロファイル
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
| `dd if=/dev/zero of=/fill bs=1M` | disk quota 5GB (overlay2 + XFS) |
| `python -c "a='x'*10**10"` | `mem_limit=4g` → OOM Killer |
| `while true; do :; done` | `cpu_quota=200000` (2 cores) |
| `curl http://169.254.169.254/` | iptables でメタデータブロック |
| `psql -h db-host` | ネットワーク隔離（backend-network 非接続） |
| `pip install malicious-pkg` | ネットワーク制限 + PyPI のみ許可 |
