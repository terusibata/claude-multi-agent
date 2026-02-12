# セキュリティ設定ガイド（Phase 2）

**作成日**: 2026-02-07
**対象**: Phase 2 - Step 7.3 (セキュリティ設定ガイド)

## 概要

Phase 2 で導入されたセキュリティ強化機能の設定・運用ガイド。

## セキュリティレイヤー

```
L1: --network none（Phase 1）
L2: Custom seccomp profile（Phase 2）
L3: --read-only + tmpfs（Phase 1）
L4: --cap-drop ALL（Phase 1）
L5: --pids-limit 256（Phase 1）
L6: --memory / --cpus（Phase 1）
L7: userns-remap（Phase 2）
L8: no-new-privileges（Phase 1）
```

## userns-remap 設定

### 概要
Docker の User Namespace Remapping により、コンテナ内の root (UID 0) をホスト上の非特権ユーザー (UID 100000+) にマッピング。

### 設定ファイル

1. **`/etc/subuid`** と **`/etc/subgid`**:
```
dockremap:100000:65536
```

2. **`/etc/docker/daemon.json`**:
```json
{
  "userns-remap": "default",
  "storage-driver": "overlay2"
}
```

### 有効化手順

```bash
# 1. 設定ファイルをコピー
sudo cp deployment/docker/subuid /etc/subuid
sudo cp deployment/docker/subgid /etc/subgid
sudo cp deployment/docker/daemon.json /etc/docker/daemon.json

# 2. Docker デーモンを再起動
sudo systemctl restart docker

# 3. アプリケーション設定を更新
# .env または環境変数で設定
USERNS_REMAP_ENABLED=true
```

### 検証

```bash
# コンテナ内のUIDがホストでリマップされているか確認
docker run --rm alpine id
# uid=0(root) gid=0(root)

# ホスト側のプロセスUID確認
ps aux | grep "containerd-shim"
# UID 100000+ で実行されていることを確認
```

### 注意事項
- 有効化すると既存のボリュームへのアクセス権が変わる
- Socket ディレクトリは `0o777` パーミッションが自動設定される（lifecycle.py）
- `UsernsMode: "host"` の場合はリマップが無効（デフォルト動作）

## Custom seccomp プロファイル

### 概要
ホワイトリスト方式のシステムコールフィルタリング。許可されていない syscall は `SCMP_ACT_ERRNO` (EPERM) で拒否。

### プロファイル場所
```
deployment/seccomp/workspace-seccomp.json
```

### 有効化

```bash
# .env または環境変数で設定
SECCOMP_PROFILE_PATH=/path/to/deployment/seccomp/workspace-seccomp.json
```

### 許可されている syscall カテゴリ

| カテゴリ | 代表的な syscall |
|---|---|
| プロセス管理 | clone, execve, fork, wait4, exit_group |
| ファイルI/O | read, write, open, close, stat, fstat |
| メモリ管理 | mmap, mprotect, brk, munmap |
| ネットワーク | socket, connect, bind（Unix socket通信用に許可。外部通信は `--network none` で遮断） |
| シグナル | rt_sigaction, rt_sigprocmask, kill |
| その他 | futex, epoll_*, pipe, dup2 |

### ブロックされている syscall（例）

| syscall | 理由 |
|---|---|
| `ptrace` | デバッグ/プロセス注入防止 |
| `mount`/`umount2` | ファイルシステム操作防止 |
| `reboot` | システム操作防止 |
| `kexec_load` | カーネル操作防止 |
| `init_module` | カーネルモジュール防止 |
| `keyctl` | カーネルキーリング操作防止 |

### seccomp 違反の監視

```promql
# 違反レート（/sec）
rate(workspace_seccomp_violations_total[5m])

# 5分間の違反数
increase(workspace_seccomp_violations_total[5m])
```

## 設定の組み合わせ

### 開発環境
```env
SECCOMP_PROFILE_PATH=
USERNS_REMAP_ENABLED=false
```
Docker デフォルトの seccomp プロファイルのみ。開発者の自由度を確保。

### ステージング環境
```env
SECCOMP_PROFILE_PATH=/app/deployment/seccomp/workspace-seccomp.json
USERNS_REMAP_ENABLED=true
```
seccomp + userns-remap を有効化し、本番環境と同等の構成で検証。互換性問題がある場合のみ `USERNS_REMAP_ENABLED=false` に変更。

### 本番環境（推奨）
```env
SECCOMP_PROFILE_PATH=/app/deployment/seccomp/workspace-seccomp.json
USERNS_REMAP_ENABLED=true
```
全レイヤーを有効化した最大セキュリティ構成。

## トラブルシューティング

### seccomp でプロセスがブロックされる
1. `workspace_seccomp_violations_total` メトリクスを確認
2. `dmesg | grep seccomp` で audit ログを確認
3. 必要な syscall を特定して `workspace-seccomp.json` に追加
4. コンテナを再起動して反映

### userns-remap でファイルアクセスエラー
1. ホスト側のファイル所有者を確認: `ls -ln /path/to/file`
2. UID/GID マッピングを確認: `cat /etc/subuid && cat /etc/subgid`
3. 必要に応じて `chown 100000:100000 /path/to/file` で所有者変更

### userns-remap 有効化後にコンテナが起動しない
1. Docker デーモンログ確認: `journalctl -u docker`
2. `/etc/docker/daemon.json` の構文確認
3. `dockremap` ユーザーが存在するか確認: `id dockremap`
