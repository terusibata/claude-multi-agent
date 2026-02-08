# seccomp Syscall分析レポート

**作成日**: 2026-02-07
**対象**: Phase 2 - Step 3.1 (必要syscall調査)
**プロファイルファイル**: `deployment/seccomp/workspace-seccomp.json`

---

## 1. 分析目的

ワークスペースコンテナで実行される主要プロセス（Python 3.11、Node.js 20、pip、git）が必要とするシステムコールを特定し、最小権限原則に基づくカスタムseccompプロファイルを設計する。

---

## 2. 分析手法

### 2.1 プロファイリング環境

| 項目 | 詳細 |
|------|------|
| コンテナイメージ | workspace-base:latest |
| ベースOS | Debian 12 (bookworm) |
| Python | 3.11.x |
| Node.js | 20.x |
| カーネル | Linux 5.15+ |
| アーキテクチャ | x86_64 (amd64) |

### 2.2 プロファイリング手法

各ワークロードに対して `strace` を使用してシステムコールをトレースし、必要なsyscallを特定しました。

```bash
# Python 3.11 の基本実行
strace -c -f python3 -c "import json, os, sys; print('hello')" 2>&1 | tail -30

# pip install（パッケージインストール）
strace -c -f pip install --user requests 2>&1 | tail -50

# Node.js 20 の基本実行
strace -c -f node -e "console.log('hello')" 2>&1 | tail -30

# git 操作
strace -c -f git status 2>&1 | tail -30
strace -c -f git clone --depth 1 https://example.com/repo.git /tmp/test-repo 2>&1 | tail -50

# 一般的なデータ処理（CSV読み込み、JSON処理）
strace -c -f python3 -c "
import csv, json, io
data = [{'a': 1, 'b': 2}]
json.dumps(data)
" 2>&1 | tail -30
```

### 2.3 分析アプローチ

1. **監査モード（SCMP_ACT_LOG）** で全ワークロードを実行し、使用されるsyscallを記録
2. 記録されたsyscallをカテゴリ別に分類
3. 各syscallの必要性を評価し、許可/拒否を決定
4. **拒否モード（SCMP_ACT_ERRNO）** に切り替え、全ワークロードの動作を検証

---

## 3. 必要syscallカテゴリ一覧

### 3.1 カテゴリ別必要syscall

| カテゴリ | syscall | 使用元 | 説明 |
|---------|---------|--------|------|
| **ファイルI/O** | read, write, open, openat | 全プロセス | 基本ファイル読み書き |
| | close, fstat, stat, lstat | 全プロセス | ファイル記述子管理・情報取得 |
| | lseek, pread64, pwrite64 | Python, Node.js | ファイルシーク・位置指定読み書き |
| | access, faccessat, faccessat2 | 全プロセス | ファイルアクセス権確認 |
| | readlink, readlinkat | Python, Node.js | シンボリックリンク読み取り |
| | getcwd, chdir, fchdir | 全プロセス | カレントディレクトリ操作 |
| | rename, renameat, renameat2 | pip, git | ファイル名変更 |
| | unlink, unlinkat, rmdir | pip, git | ファイル・ディレクトリ削除 |
| | mkdir, mkdirat | pip, git | ディレクトリ作成 |
| | symlink, symlinkat | pip | シンボリックリンク作成 |
| | chmod, fchmod, fchmodat | pip, git | ファイル権限変更 |
| | chown, fchown, fchownat | pip | ファイル所有者変更 |
| | utimensat, futimesat | pip, git | タイムスタンプ変更 |
| | statfs, fstatfs | Python, pip | ファイルシステム情報取得 |
| | getdents, getdents64 | 全プロセス | ディレクトリエントリ読み取り |
| | fcntl | 全プロセス | ファイル記述子制御 |
| | dup, dup2, dup3 | 全プロセス | ファイル記述子複製 |
| | pipe, pipe2 | 全プロセス | パイプ作成 |
| | ioctl | 全プロセス | デバイス制御（ターミナル操作含む） |
| | flock | pip, git | ファイルロック |
| | ftruncate | Python, pip | ファイル切り詰め |
| | fallocate | Python | ファイル領域事前確保 |
| | copy_file_range | pip | 効率的ファイルコピー |
| **プロセス管理** | clone, clone3 | 全プロセス | プロセス/スレッド作成 |
| | execve, execveat | pip, git, Node.js | プログラム実行 |
| | wait4, waitid | pip, git | 子プロセス待機 |
| | exit, exit_group | 全プロセス | プロセス終了 |
| | getpid, getppid, gettid | 全プロセス | プロセスID取得 |
| | getuid, getgid, geteuid, getegid | 全プロセス | ユーザー/グループID取得 |
| | getgroups | Python | 補助グループ取得 |
| | set_tid_address | 全プロセス | スレッドID設定 |
| | set_robust_list, get_robust_list | 全プロセス | ロバストfutexリスト |
| | prctl | Python, Node.js | プロセス制御 |
| | arch_prctl | 全プロセス | アーキテクチャ固有設定 |
| | sched_getaffinity, sched_yield | Python, Node.js | スケジューリング |
| | getrlimit, setrlimit, prlimit64 | 全プロセス | リソース制限取得/設定 |
| | vfork | git | 軽量プロセス作成 |
| **メモリ管理** | mmap, munmap | 全プロセス | メモリマッピング |
| | mprotect | 全プロセス | メモリ保護属性変更 |
| | brk | 全プロセス | データセグメント拡張 |
| | mremap | Python | メモリリマップ |
| | madvise | Python, Node.js | メモリアドバイス |
| | mlock, munlock | Node.js | メモリロック |
| | msync | Python | メモリ同期 |
| **ネットワーク (AF_UNIXのみ)** | socket | 全プロセス | ソケット作成（AF_UNIXのみ許可） |
| | connect | 全プロセス | ソケット接続 |
| | sendto, recvfrom | 全プロセス | データ送受信 |
| | sendmsg, recvmsg | 全プロセス | メッセージ送受信 |
| | bind, listen, accept, accept4 | workspace_agent | ソケットリスン（UDS用） |
| | getsockname, getpeername | 全プロセス | ソケットアドレス取得 |
| | setsockopt, getsockopt | 全プロセス | ソケットオプション |
| | shutdown | 全プロセス | ソケット切断 |
| | socketpair | Python | ソケットペア作成 |
| **時間** | clock_gettime, clock_getres | 全プロセス | 時刻取得 |
| | gettimeofday | 全プロセス | 時刻取得（レガシー） |
| | nanosleep, clock_nanosleep | 全プロセス | スリープ |
| | timer_create, timer_settime, timer_delete | Python, Node.js | POSIXタイマー |
| | timerfd_create, timerfd_settime | Node.js | タイマーファイル記述子 |
| **シグナル** | rt_sigaction, rt_sigprocmask | 全プロセス | シグナルハンドラ設定 |
| | rt_sigreturn | 全プロセス | シグナルハンドラからの復帰 |
| | kill, tgkill, tkill | Python, Node.js | シグナル送信 |
| | sigaltstack | Python, Node.js | 代替シグナルスタック |
| **I/O多重化** | epoll_create, epoll_create1 | Python, Node.js | epollインスタンス作成 |
| | epoll_ctl, epoll_wait, epoll_pwait | Python, Node.js | epoll制御・待機 |
| | poll, ppoll | 全プロセス | ポーリング |
| | select, pselect6 | Python | セレクト |
| | eventfd, eventfd2 | Node.js | イベント通知 |
| **その他** | futex | 全プロセス | 高速ユーザー空間ミューテックス |
| | getrandom | 全プロセス | 乱数取得 |
| | uname | 全プロセス | システム情報取得 |
| | sysinfo | Python | システム情報取得 |
| | newfstatat | 全プロセス | ファイル情報取得（AT対応） |
| | rseq | 全プロセス | Restartable sequences |
| | memfd_create | Python | 匿名メモリファイル作成 |
| | landlock_create_ruleset | Python | Landlockセキュリティ |
| | statx | pip, git | 拡張ファイル情報取得 |

---

## 4. 明示的ブロック対象syscall

以下のsyscallはデフォルトアクション（SCMP_ACT_ERRNO）により拒否されますが、特に重要なため明示的に記載します。

### 4.1 ブロック対象一覧

| syscall | カテゴリ | ブロック理由 | リスクレベル |
|---------|---------|-------------|-------------|
| `mount` | ファイルシステム | ファイルシステムのマウント変更を防止。コンテナエスケープの手段となり得る | 致命的 |
| `umount2` | ファイルシステム | ファイルシステムのアンマウントを防止。セキュリティマウントの解除を防ぐ | 致命的 |
| `reboot` | システム操作 | システム再起動を防止 | 致命的 |
| `kexec_load` | システム操作 | カーネルの動的ロードを防止。rootkit注入の手段となり得る | 致命的 |
| `ptrace` | デバッグ | 他プロセスのメモリ読み取り・操作を防止。認証情報の窃取やプロセスインジェクションを防ぐ | 高 |
| `init_module` / `finit_module` | カーネルモジュール | カーネルモジュールのロードを防止。rootkit注入を防ぐ | 致命的 |
| `delete_module` | カーネルモジュール | カーネルモジュールのアンロードを防止 | 致命的 |
| `pivot_root` | ファイルシステム | ルートファイルシステムの変更を防止。コンテナエスケープの手段 | 致命的 |
| `swapon` / `swapoff` | メモリ管理 | スワップ領域の操作を防止 | 中 |
| `settimeofday` / `clock_settime` | 時間操作 | システム時刻の変更を防止。ログの改ざんやTLS証明書検証の回避を防ぐ | 高 |
| `socket(AF_INET)` | ネットワーク | IPv4ネットワークソケットの作成を防止。`--network none` との二重防御 | 高 |
| `socket(AF_INET6)` | ネットワーク | IPv6ネットワークソケットの作成を防止。`--network none` との二重防御 | 高 |
| `acct` | システム操作 | プロセスアカウンティングの操作を防止 | 中 |
| `add_key` / `keyctl` / `request_key` | カーネルキーリング | カーネルキーリングの操作を防止 | 中 |
| `bpf` | カーネル | BPFプログラムのロードを防止 | 高 |
| `unshare` | 名前空間 | 新しい名前空間の作成を防止。権限昇格の手段 | 高 |
| `setns` | 名前空間 | 既存の名前空間への参加を防止 | 高 |
| `userfaultfd` | メモリ管理 | ユーザー空間ページフォルトハンドリングを防止。エクスプロイトに悪用される | 高 |
| `perf_event_open` | パフォーマンス | パフォーマンスモニタリングを防止。サイドチャネル攻撃に悪用される可能性 | 中 |
| `personality` | プロセス | 実行ドメインの変更を防止 | 中 |

### 4.2 socket syscallの条件付きフィルタリング

`socket` syscallは第1引数（domain）によって許可/拒否を判定します:

```json
{
    "names": ["socket"],
    "action": "SCMP_ACT_ALLOW",
    "args": [
        {
            "index": 0,
            "value": 1,
            "op": "SCMP_CMP_EQ"
        }
    ],
    "comment": "AF_UNIX (1) のみ許可"
}
```

- `AF_UNIX` (1): **許可** - Unix Domain Socket通信（Proxy/Agent間通信に必須）
- `AF_INET` (2): **拒否** - IPv4ネットワーク通信（`--network none` と二重防御）
- `AF_INET6` (10): **拒否** - IPv6ネットワーク通信（`--network none` と二重防御）
- `AF_NETLINK` (16): **拒否** - カーネルとの通信
- その他: **拒否**

---

## 5. テストアプローチ

### 5.1 段階的導入

```
Phase A: 監査モード（SCMP_ACT_LOG）
  ↓ 1週間の監視期間
Phase B: 部分適用（一部ホストのみ SCMP_ACT_ERRNO）
  ↓ 1週間の監視期間
Phase C: 全面適用（全ホスト SCMP_ACT_ERRNO）
```

### 5.2 Phase A: 監査モード

```json
{
    "defaultAction": "SCMP_ACT_LOG",
    "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
    "syscalls": [
        {
            "names": ["mount", "umount2", "reboot", "kexec_load", "ptrace"],
            "action": "SCMP_ACT_ERRNO",
            "errnoRet": 1,
            "comment": "致命的syscallは監査モードでもブロック"
        }
    ]
}
```

監査ログの確認:

```bash
# カーネルログからseccomp違反を確認
dmesg | grep seccomp
journalctl -k | grep seccomp

# 使用されたsyscallの集計
dmesg | grep "seccomp" | awk '{print $NF}' | sort | uniq -c | sort -rn
```

### 5.3 Phase B: 拒否モード（部分適用）

```json
{
    "defaultAction": "SCMP_ACT_ERRNO",
    "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
    "syscalls": [
        {
            "names": ["read", "write", "open", "close", "..."],
            "action": "SCMP_ACT_ALLOW",
            "comment": "許可syscallリスト"
        }
    ]
}
```

### 5.4 テストスイート

各フェーズで以下のテストを実行します:

```bash
# 1. Python基本実行テスト
docker run --rm --security-opt seccomp=deployment/seccomp/workspace-seccomp.json \
    workspace-base:latest python3 -c "
import json, os, sys, hashlib, csv, io, tempfile
print('Python basic: OK')
# ファイル操作
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir='/workspace', delete=False) as f:
    json.dump({'test': True}, f)
print('File I/O: OK')
# ハッシュ計算
hashlib.sha256(b'test').hexdigest()
print('Crypto: OK')
"

# 2. pip install テスト
docker run --rm --security-opt seccomp=deployment/seccomp/workspace-seccomp.json \
    workspace-base:latest pip install --user --no-cache-dir requests==2.31.0
# 期待値: Successfully installed

# 3. Node.js テスト
docker run --rm --security-opt seccomp=deployment/seccomp/workspace-seccomp.json \
    workspace-base:latest node -e "
const fs = require('fs');
const crypto = require('crypto');
console.log('Node.js basic: OK');
fs.writeFileSync('/workspace/test.json', JSON.stringify({test: true}));
console.log('File I/O: OK');
crypto.createHash('sha256').update('test').digest('hex');
console.log('Crypto: OK');
"

# 4. git テスト
docker run --rm --security-opt seccomp=deployment/seccomp/workspace-seccomp.json \
    workspace-base:latest bash -c "
cd /workspace && git init test-repo && cd test-repo
git config user.email 'test@test.com'
git config user.name 'test'
echo 'hello' > README.md
git add . && git commit -m 'init'
echo 'Git operations: OK'
"

# 5. Unix Socket通信テスト
docker run --rm --security-opt seccomp=deployment/seccomp/workspace-seccomp.json \
    workspace-base:latest python3 -c "
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
print('AF_UNIX socket: OK')
s.close()
"

# 6. ネットワークソケット拒否テスト（期待: 失敗すること）
docker run --rm --security-opt seccomp=deployment/seccomp/workspace-seccomp.json \
    workspace-base:latest python3 -c "
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print('ERROR: AF_INET socket should be blocked')
    exit(1)
except OSError:
    print('AF_INET socket blocked: OK (expected)')
"
```

---

## 6. プロファイル配置場所

### 6.1 ファイルパス

```
deployment/seccomp/workspace-seccomp.json
```

### 6.2 プロファイル構造

```json
{
    "defaultAction": "SCMP_ACT_ERRNO",
    "architectures": [
        "SCMP_ARCH_X86_64",
        "SCMP_ARCH_X86",
        "SCMP_ARCH_X32"
    ],
    "syscalls": [
        {
            "names": ["許可syscallリスト"],
            "action": "SCMP_ACT_ALLOW"
        },
        {
            "names": ["socket"],
            "action": "SCMP_ACT_ALLOW",
            "args": [
                {
                    "index": 0,
                    "value": 1,
                    "op": "SCMP_CMP_EQ"
                }
            ],
            "comment": "AF_UNIX only"
        }
    ]
}
```

### 6.3 アプリケーション設定

```python
# app/config.py
SECCOMP_PROFILE_PATH = os.getenv("SECCOMP_PROFILE_PATH", "")

# app/services/container/config.py
if settings.seccomp_profile_path:
    security_opt.append(f"seccomp={settings.seccomp_profile_path}")
```

---

## 7. 継続的メンテナンス

### 7.1 プロファイル更新が必要なケース

| トリガー | 対応 |
|---------|------|
| ベースイメージのアップデート（Python/Node.jsバージョン変更） | strace再実行、syscallリスト更新 |
| 新しいPythonパッケージの追加 | 当該パッケージのsyscallプロファイリング |
| コンテナ内ワークフローの変更 | 変更箇所のsyscallプロファイリング |
| seccomp違反アラートの発生 | 違反syscallの分析と許可/対応判定 |

### 7.2 CI/CDへの統合

```yaml
# テストパイプラインに組み込むseccomp検証ジョブ
seccomp-test:
  script:
    - docker build -t workspace-base:test .
    - ./tests/run-seccomp-tests.sh deployment/seccomp/workspace-seccomp.json
  on_failure:
    - echo "seccompプロファイルの更新が必要です"
```

### 7.3 監視連携

- `workspace_seccomp_violations_total` メトリクスで違反数を監視
- 違反発生時はカーネルログ（`dmesg`）から詳細を確認
- 想定外のsyscall使用があれば、プロファイル更新の要否を判定
