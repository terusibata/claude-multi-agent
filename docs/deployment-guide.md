# デプロイメント設定ガイド

`deployment/` ディレクトリには、ワークスペースコンテナのセキュリティ強化と S3 ストレージ管理に関する設定ファイルが含まれています。

## ディレクトリ構成

```
deployment/
├── docker/                  # Docker デーモン設定
│   ├── daemon.json          # userns-remap 等のデーモン設定
│   ├── subuid               # UID マッピング
│   └── subgid               # GID マッピング
├── seccomp/                 # seccomp プロファイル
│   └── workspace-seccomp.json  # 許可するシステムコールのホワイトリスト
├── apparmor/                # AppArmor プロファイル
│   └── workspace-container  # コンテナのファイルアクセス制限
└── s3/                      # S3 ライフサイクル設定
    ├── lifecycle-policy.json # ライフサイクルルール定義
    ├── apply-lifecycle.sh   # 適用スクリプト
    └── README.md            # S3 設定の詳細
```

## アーキテクチャとの関係

本システムでは、会話ごとに隔離された Docker コンテナ内で Claude Agent SDK を実行します。
`deployment/` の設定はこのワークスペースコンテナのセキュリティを多層的に強化するものです。

```
ホスト (FastAPI バックエンド)
  │
  │  Unix Socket 経由で SSE 中継
  │
  ▼
┌────────────────────────────────────────────────────────┐
│  ワークスペースコンテナ（会話ごとに1つ）                  │
│                                                        │
│  適用されるセキュリティ:                                 │
│  ├── userns-remap      (Docker daemon: UID/GIDリマップ) │
│  ├── seccomp           (システムコール制限)              │
│  ├── AppArmor          (ファイルアクセス制限)            │
│  ├── no-new-privileges (権限昇格の防止)                 │
│  ├── read-only rootfs  (ルートFS書き込み禁止)            │
│  ├── network: none     (ネットワーク隔離)               │
│  ├── cap-drop ALL      (全Capability剥奪)              │
│  │   └── cap-add: CHOWN, SETUID, SETGID, DAC_OVERRIDE │
│  ├── PID制限           (256プロセス)                    │
│  ├── メモリ制限         (2GB, swap無効)                  │
│  ├── CPU制限           (2コア)                          │
│  └── ディスク制限       (5GB)                           │
└────────────────────────────────────────────────────────┘
```

各セキュリティ層の設定値は `app/config.py` で管理され、`app/services/container/config.py` でコンテナ作成時に適用されます。

```python
# app/config.py の関連設定
seccomp_profile_path: str = "deployment/seccomp/workspace-seccomp.json"
apparmor_profile_name: str = "workspace-container"
userns_remap_enabled: bool = True
```

---

## 1. Docker デーモン設定 (`deployment/docker/`)

### 概要

Docker デーモンの `userns-remap` を有効化し、コンテナ内の root ユーザーをホスト上の非特権ユーザーにマッピングします。
これにより、コンテナからエスケープされた場合でもホストの root 権限を取得できません。

### ファイル

| ファイル | 説明 |
|---------|------|
| `daemon.json` | Docker デーモン設定（userns-remap, overlay2, ログ設定） |
| `subuid` | UID マッピング（`dockremap:100000:65536`） |
| `subgid` | GID マッピング（`dockremap:100000:65536`） |

### 適用手順

```bash
# 1. dockremap ユーザーを作成
sudo groupadd -r dockremap
sudo useradd -r -g dockremap dockremap

# 2. subuid / subgid を配置
sudo cp deployment/docker/subuid /etc/subuid
sudo cp deployment/docker/subgid /etc/subgid

# 3. daemon.json を配置
sudo cp deployment/docker/daemon.json /etc/docker/daemon.json

# 4. Docker を再起動
sudo systemctl restart docker
```

### 確認

```bash
# userns-remap が有効か確認
docker info | grep "User Namespace"
# 出力例: User Namespace Remapping: dockremap

# コンテナ内の root がホスト上では別ユーザーか確認
docker run --rm alpine id
# UID は 0 だが、ホスト上では 100000 にマッピングされている
```

### 注意事項

- userns-remap を有効にすると、既存のコンテナ・イメージのストレージパスが変わります
- 既に動作中の環境に適用する場合は、事前にイメージの再プルが必要です
- `userns_remap_enabled: false` に設定すれば、アプリケーション側で userns-remap 前提の動作を無効化できます

---

## 2. seccomp プロファイル (`deployment/seccomp/`)

### 概要

コンテナ内のプロセスが実行できるシステムコールをホワイトリスト方式で制限します。
デフォルトポリシーは `SCMP_ACT_ERRNO`（拒否）で、許可するシステムコールのみを明示的にリストアップしています。

### 許可されるシステムコール

| カテゴリ | 例 | 用途 |
|---------|------|------|
| **File I/O** | `read`, `write`, `open`, `stat`, `mkdir`, `unlink` | ファイル操作全般 |
| **Process** | `clone`, `execve`, `wait4`, `exit`, `getpid` | プロセス生成・実行 |
| **Memory** | `mmap`, `mprotect`, `munmap`, `brk` | メモリ管理 |
| **Network** | `socket`, `connect`, `bind`, `epoll_*` | Unix ソケット通信、イベント通知 |
| **Pipe/Signal** | `pipe`, `kill`, `rt_sigaction` | プロセス間通信 |
| **Time** | `clock_gettime`, `nanosleep` | 時刻取得、スリープ |
| **System** | `uname`, `getrandom`, `getrlimit` | カーネル情報、乱数 |

### 明示的に拒否されるシステムコール

| システムコール | 拒否理由 |
|---------------|---------|
| `mount`, `umount2` | ファイルシステムの変更を防止 |
| `reboot`, `kexec_load` | ホストの再起動を防止 |
| `ptrace` | 他プロセスのデバッグ・操作を防止 |
| `init_module`, `finit_module` | カーネルモジュールの読み込みを防止 |
| `pivot_root` | ルートファイルシステムの変更を防止 |
| `settimeofday`, `clock_settime` | システム時刻の改竄を防止 |

### 適用方法

seccomp プロファイルは**コンテナ作成時に自動適用**されます（`app/config.py` の `seccomp_profile_path` で参照）。
手動でのホスト側設定は不要です。

```bash
# 設定確認
grep seccomp_profile_path app/config.py

# 無効化する場合（開発環境向け）
# .env に以下を追加:
SECCOMP_PROFILE_PATH=""
```

### 動作確認

```bash
# seccomp が適用されたコンテナで禁止操作を試行
docker run --rm --security-opt seccomp=deployment/seccomp/workspace-seccomp.json alpine mount /dev/sda /mnt
# → "Operation not permitted" が返る
```

---

## 3. AppArmor プロファイル (`deployment/apparmor/`)

### 概要

AppArmor は Linux カーネルのセキュリティモジュールで、プロセスごとにファイルアクセスやネットワーク操作を制限します。
seccomp がシステムコールレベルの制限であるのに対し、AppArmor はファイルパスレベルの制限を提供します。

### アクセスルール

#### 読み書き許可

| パス | 権限 | 用途 |
|------|------|------|
| `/workspace/**` | `rw` | ワークスペースファイル（ユーザーのコード等） |
| `/opt/venv/**` | `rw` | Python 仮想環境 |
| `/tmp/**`, `/var/tmp/**` | `rw` | 一時ファイル |
| `/home/appuser/**` | `rw` | ユーザーホーム・キャッシュ |
| `/var/run/ws/**`, `/var/run/agent.sock` | `rw` | Unix ソケット通信 |

#### 読み取りのみ許可

| パス | 用途 |
|------|------|
| `/usr/**`, `/lib/**`, `/etc/**` | システムライブラリ・設定 |
| `/opt/workspace_agent/**` | エージェントコード |
| `/proc/cpuinfo`, `/proc/meminfo` 等 | 限定的な proc 情報 |

#### 明示的に拒否

| パス / 操作 | 拒否理由 |
|------------|---------|
| `/etc/shadow`, `/etc/gshadow` | パスワード情報の漏洩防止 |
| `/etc/sudoers` | 権限昇格の防止 |
| `/proc/*/mem`, `/proc/kcore` | カーネルメモリの読み取り防止 |
| `/sys/** w` | カーネルパラメータの変更防止 |
| `mount`, `umount`, `pivot_root` | マウント操作の防止 |
| `ptrace` | プロセスデバッグの防止 |

### 適用手順

```bash
# 1. プロファイルを AppArmor にロード
sudo apparmor_parser -r deployment/apparmor/workspace-container

# 2. 確認
sudo aa-status | grep workspace-container
# 出力例:
#   workspace-container (enforce)

# 3. （オプション）自動起動時に読み込まれるように配置
sudo cp deployment/apparmor/workspace-container /etc/apparmor.d/workspace-container
sudo systemctl reload apparmor
```

### 無効化（開発環境向け）

```bash
# .env に以下を追加:
APPARMOR_PROFILE_NAME=""
```

### 注意事項

- AppArmor は Ubuntu / Debian 系で利用可能です（RHEL / CentOS では SELinux が代替）
- `aa-status` が `enforce` モードで表示されていることを確認してください
- 新しいツールやランタイムを追加する場合は、プロファイルの更新が必要な場合があります

---

## 4. S3 ライフサイクル設定 (`deployment/s3/`)

### 概要

ワークスペースファイルの長期保管コストを最適化する S3 ライフサイクルポリシーです。

### ライフサイクルルール

```
作成 ──→ 30日 ──→ 90日 ──→ 270日
         │        │        │
         │        ▼        ▼
         │     Glacier   完全削除
         │     移行
         ▼
      非最新バージョン
      自動削除
```

| ルール | 対象 | 条件 | 動作 |
|--------|------|------|------|
| 非最新バージョン削除 | `workspaces/` | 30日経過 | 旧バージョンを自動削除 |
| Glacier 移行 | `workspaces/` | 90日経過 | Glacier ストレージクラスに移行 |
| 完全削除 | `workspaces/` | 270日経過 | オブジェクトを完全に削除 |

### 適用手順

```bash
# S3 バケットにライフサイクルポリシーを適用
./deployment/s3/apply-lifecycle.sh <bucket-name>

# 例:
./deployment/s3/apply-lifecycle.sh my-workspace-bucket
```

スクリプトは以下を実行します：

1. バケットの存在確認
2. バケットバージョニングの有効化
3. ライフサイクルポリシーの適用
4. 適用結果の出力

### 適用確認

```bash
aws s3api get-bucket-lifecycle-configuration --bucket <bucket-name>
```

### 注意事項

- バージョニング有効化は不可逆です（Suspended にはできるが、完全無効化は不可）
- Glacier からの復元には数時間〜数日かかります
- 本番環境への適用前にステージング環境でのテストを推奨

---

## 5. リソース制限（`app/config.py`）

### 概要

コンテナに対するリソース制限は `app/services/container/config.py` でコンテナ作成時に自動適用されます。
デフォルト値は `app/config.py` で管理されており、環境変数で変更可能です。

### 設定項目

| 設定 | 環境変数 | デフォルト値 | 説明 |
|------|---------|------------|------|
| Capability | - | CapDrop ALL, CapAdd 最小限 | 全Capabilityを剥奪し、CHOWN, SETUID, SETGID, DAC_OVERRIDEのみ復元 |
| PID制限 | `CONTAINER_PIDS_LIMIT` | 256 | コンテナ内の最大プロセス数（fork bomb対策）。SDK CLIサブプロセス + socat を考慮した値 |
| メモリ制限 | `CONTAINER_MEMORY_LIMIT` | 2GB | コンテナの最大メモリ。MemorySwap = Memory でswapを無効化 |
| CPU制限 | `CONTAINER_CPU_QUOTA` | 200000 (2コア) | CpuPeriod=100000に対するクォータ。200000 = 2コア分 |
| ディスク制限 | `CONTAINER_DISK_LIMIT` | 5G | コンテナのストレージ上限（Docker storage driver の StorageOpt） |

### Capability ポリシー

```
CapDrop: ALL           # 全38種のLinux Capabilityを剥奪
CapAdd:
  - CHOWN              # ファイル所有権の変更（パッケージインストール時に必要）
  - SETUID             # プロセスのUID変更（su/sudo等で使用）
  - SETGID             # プロセスのGID変更
  - DAC_OVERRIDE       # ファイルパーミッションのバイパス（root操作時に必要）
```

### Tmpfs マウント

read-only rootfs と組み合わせて、書き込み可能な領域をTmpfsで提供します:

| パス | サイズ | オプション | 用途 |
|------|-------|----------|------|
| `/tmp` | 512M | `rw,noexec,nosuid` | 一時ファイル |
| `/var/tmp` | 256M | `rw,noexec,nosuid` | 一時ファイル |
| `/run` | 64M | `rw,noexec,nosuid` | ランタイムデータ |
| `/home/appuser/.cache` | 512M | `rw,noexec,nosuid` | キャッシュ（pip等） |
| `/home/appuser` | 128M | `rw,noexec,nosuid` | ユーザーホーム |
| `/workspace` | 1G | `rw,nosuid` | 作業ディレクトリ（コード実行あり） |

---

## 環境別の推奨設定

| 設定 | 開発環境 | ステージング | 本番環境 |
|------|---------|------------|---------|
| userns-remap | 任意 | 有効 | **必須** |
| seccomp | 空文字で無効化可 | 有効 | **必須** |
| AppArmor | 空文字で無効化可 | 有効 | **必須** |
| S3 ライフサイクル | 不要 | 適用推奨 | **必須** |
| read-only rootfs | 任意 | 有効 | **必須** |
| network: none | 任意 | 有効 | **必須** |
| cap-drop ALL | 有効 | 有効 | **必須** |
| PID制限 | 有効 (256) | 有効 (256) | **必須** |
| メモリ制限 | 有効 (2GB) | 有効 (2GB) | **必須** |
| CPU制限 | 有効 (2コア) | 有効 (2コア) | **必須** |
| ディスク制限 | 有効 (5G) | 有効 (5G) | **必須** |

### 開発環境での無効化例

```bash
# .env
SECCOMP_PROFILE_PATH=""
APPARMOR_PROFILE_NAME=""
USERNS_REMAP_ENABLED=false
```

---

## トラブルシューティング

### コンテナ起動に失敗する

```
OCI runtime error: ... seccomp profile not found
```

**原因**: seccomp プロファイルのパスが正しくない

**解決策**: `deployment/seccomp/workspace-seccomp.json` がアプリケーションのワーキングディレクトリから参照可能か確認

```bash
ls -la deployment/seccomp/workspace-seccomp.json
```

### AppArmor でプロセスがブロックされる

```
apparmor="DENIED" operation="open" ...
```

**原因**: プロファイルに許可されていないパスへのアクセス

**解決策**: `dmesg` または `/var/log/syslog` で拒否ログを確認し、必要に応じてプロファイルを更新

```bash
# 拒否ログの確認
sudo dmesg | grep apparmor | tail -20

# complain モードで検証（拒否せずログのみ）
sudo aa-complain workspace-container

# 修正後に enforce モードに戻す
sudo aa-enforce workspace-container
```

### userns-remap でボリュームの権限エラー

```
Permission denied: '/workspace/...'
```

**原因**: userns-remap により、コンテナ内の UID がホスト上で異なる UID にマッピングされている

**解決策**: ホスト側のボリュームディレクトリの所有者を `100000:100000` に変更

```bash
sudo chown -R 100000:100000 /path/to/volume
```
