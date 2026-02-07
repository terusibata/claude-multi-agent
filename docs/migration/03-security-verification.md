# セキュリティ検証レポート

**作成日**: 2026-02-07
**対象**: Workspace Container Isolation (Phase 1)

---

## 1. 脅威モデルと対策マトリクス

| 脅威ID | 脅威 | 対策 | 実装ファイル | ステータス |
|--------|------|------|-------------|-----------|
| T-1 | `rm -rf /` によるホスト破壊 | `--network none`, read-only rootfs, コンテナ隔離 | `container/config.py` | :white_check_mark: |
| T-2 | Fork bomb / リソース枯渇 | PID制限(100), CPU制限(200000), メモリ制限(2GB) | `container/config.py` | :white_check_mark: |
| T-3 | テナント間ファイルアクセス | 会話ごと独立コンテナ、`/workspace`マウント分離 | `container/orchestrator.py` | :white_check_mark: |
| T-4 | pip install による環境汚染 | 隔離コンテナ、tmpfs上の書き込み | `workspace-base/Dockerfile` | :white_check_mark: |
| T-5 | 任意の外部通信 | `--network none`, Proxy経由のみ通信可能 | `container/config.py` | :white_check_mark: |
| T-6 | AWS認証情報漏洩 | 環境変数に認証情報なし、Proxy SigV4注入 | `proxy/credential_proxy.py` | :white_check_mark: |
| T-7 | メタデータサービス（169.254.169.254）アクセス | `--network none` で完全遮断 | `container/config.py` | :white_check_mark: |
| T-8 | コンテナエスケープ | seccomp, no-new-privileges, read-only rootfs | `container/config.py` | :white_check_mark: |

---

## 2. セキュリティレイヤー確認

### Layer 1: ネットワーク隔離

- **実装**: `NetworkMode: "none"` (`container/config.py:31`)
- **効果**: コンテナは外部ネットワークへの一切のアクセスが不可能
- **通信手段**: Unix Domain Socket のみ（`/var/run/agent.sock`, `/var/run/proxy.sock`）

### Layer 2: リソース制限

- **CPU**: `CpuQuota: 200000` (2コア相当)
- **メモリ**: `Memory: 2GB` (2 * 1024³ bytes)
- **PIDs**: `PidsLimit: 100`
- **ディスク**: tmpfs上のサイズ制限

### Layer 3: ファイルシステム保護

- **Read-only rootfs**: `ReadonlyRootfs: True`
- **書き込み可能領域**: `/workspace` (bind mount), `/tmp` (tmpfs, noexec)
- **noexec tmpfs**: `/tmp` にバイナリ実行不可フラグ

### Layer 4: 認証情報保護

- **AWS認証情報**: ホスト側 `app/main.py` から `os.environ` への直接注入を完全除去
- **SigV4 Proxy**: `CredentialInjectionProxy` が Bedrock API リクエストにのみ認証情報を注入
- **ドメインホワイトリスト**: `bedrock-runtime.*.amazonaws.com` のみ許可

### Layer 5: プロセスセキュリティ

- **SecurityOpt**: `no-new-privileges` - 権限昇格防止
- **Seccomp**: デフォルトseccompプロファイル適用
- **非rootユーザー**: UID 1000 (`appuser`) でプロセス実行

### Layer 6: コンテナライフサイクル管理

- **TTL管理**: 非アクティブTTL (60分), 絶対TTL (8時間)
- **GC**: 定期的なガベージコレクション（デフォルト60秒間隔）
- **グレースフルシャットダウン**: アプリケーション終了時に全コンテナ破棄

---

## 3. 認証情報フロー確認

### Before（旧アーキテクチャ）

```
app/main.py
  └─ os.environ["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id     ← 全プロセスに公開
  └─ os.environ["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key ← 全プロセスに公開
     └─ Claude Agent SDK（全セッション共有プロセス内）← 認証情報アクセス可能
```

### After（新アーキテクチャ）

```
app/main.py
  └─ ContainerOrchestrator
     └─ CredentialInjectionProxy（ホスト側プロセス）
        ├─ AWS認証情報: メモリ内のみ保持
        ├─ ドメインホワイトリスト検証
        └─ SigV4署名をリクエストヘッダーに注入
           │
           │ Unix Socket (proxy.sock)
           ▼
        Container (--network none)
        └─ workspace_agent
           └─ HTTP_PROXY=unix:///var/run/proxy.sock
              └─ Bedrock API リクエスト → Proxy経由で署名付きリクエスト送信
```

**結果**: コンテナ内プロセスはAWS認証情報に一切アクセスできない

---

## 4. 残存リスクと推奨対策

| リスク | 重要度 | 推奨対策 | Phase | ステータス |
|--------|-------|---------|-------|----------|
| Docker Socketアクセス | 中 | AppArmorプロファイル、Docker API制限 | 3 | 未対応 |
| コンテナイメージの脆弱性 | 中 | 定期的なイメージスキャン (Trivy等) | 3 | 未対応 |
| カスタムseccompプロファイル | 低 | 最小権限原則に基づくシステムコール制限 | 2 | **Phase 2 実装済** |
| userns-remap | 低 | User Namespace分離 | 2 | **Phase 2 実装済** |
| gVisor/Firecracker | 低 | より強力なランタイム隔離 | 3 | 未対応 |

---

## 5. Phase 2 セキュリティレイヤー追加

### Layer 2: カスタムseccomp プロファイル (Phase 2)

- **実装**: `deployment/seccomp/workspace-seccomp.json`
- **設定**: `SECCOMP_PROFILE_PATH` 環境変数 → `container/config.py` SecurityOpt に適用
- **方式**: ホワイトリスト方式（`SCMP_ACT_ERRNO` デフォルト）
- **許可対象**: Python 3.11 + Node.js 20 + pip + git + データ処理に必要なsyscallのみ
- **明示ブロック**: mount, umount2, reboot, kexec_load, ptrace, init_module, pivot_root, socket(AF_INET/AF_INET6)
- **二重防御**: `--network none` + seccompでsocket(AF_INET)もブロック
- **分析レポート**: `docs/migration/07-seccomp-syscall-analysis.md`

### Layer 7: userns-remap (Phase 2)

- **実装**: `deployment/docker/daemon.json` + `subuid`/`subgid`
- **設定**: `USERNS_REMAP_ENABLED=true` + Docker daemon設定
- **効果**: コンテナ内root (UID 0) → ホスト上の非特権ユーザー (UID 100000+)
- **ソケット権限**: `lifecycle.py` でuserns-remap有効時にソケットディレクトリ権限を0o777に調整
- **検証手順**: `docs/migration/05-userns-remap-verification.md`
- **デプロイ手順**: `docs/migration/06-userns-remap-deployment.md`

---

## 6. 検証チェックリスト

### Phase 1

- [x] AWS認証情報が環境変数から除去されていること
- [x] コンテナが `--network none` で起動されること
- [x] PID/CPU/メモリ制限が設定されていること
- [x] Read-only rootfsが有効であること
- [x] tmpfsにnoexecフラグが設定されていること
- [x] CredentialInjectionProxyがドメインホワイトリストを適用すること
- [x] SigV4署名がProxy側でのみ実行されること
- [x] GCがTTL超過コンテナを適切に破棄すること
- [x] グレースフルシャットダウンで全コンテナが破棄されること
- [x] ヘルスチェックにコンテナシステム状態が含まれること

### Phase 2

- [x] カスタムseccompプロファイルが作成されていること (`deployment/seccomp/workspace-seccomp.json`)
- [x] seccompプロファイルパスがコンテナ設定に反映されること (`SECCOMP_PROFILE_PATH`)
- [x] userns-remap設定ファイルが作成されていること (`deployment/docker/daemon.json`)
- [x] userns-remap有効時にソケットディレクトリ権限が調整されること
- [x] Prometheusメトリクスでセキュリティ関連イベントが記録されること
- [x] seccomp違反メトリクス (`workspace_seccomp_violations_total`) が定義されていること
- [x] 監視アラートにセキュリティ関連ルールが含まれること
- [x] Phase 2統合テストが作成されていること (`tests/integration/test_phase2.py`)
