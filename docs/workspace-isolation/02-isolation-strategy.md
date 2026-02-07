# 2. 隔離戦略の設計

## 2.1 技術選定

### 比較評価

| 技術 | 隔離強度 | 起動速度 | オーバーヘッド | 運用複雑度 | 適合性 |
|------|---------|---------|-------------|-----------|--------|
| Docker コンテナ (標準) | 中 | ~500ms | 低 | 低 | **推奨 (Phase 1)** |
| Docker + gVisor | 高 | ~600ms | 中 (I/O: 10-30%) | 中 | **推奨 (Phase 2)** |
| Firecracker MicroVM | 最高 | ~125ms | 極低 (<5MiB) | 高 | Phase 3 検討 |
| Kata Containers | 最高 | ~200ms | 低 | 高 | 代替案 |
| nsjail | 中-高 | ~10ms | 極低 | 中 | 補助ツール |
| venv / virtualenv | なし(依存分離のみ) | ~1s | 低 | 低 | 不十分 |

### 推奨: Docker コンテナ + gVisor 段階導入

**理由**:

1. **既存インフラとの整合性**: 本システムは既に Docker Compose ベース。追加のVMインフラ不要
2. **運用チームの学習コスト**: Docker エコシステム内で完結
3. **段階的強化可能**: 標準コンテナ → gVisor → Firecracker と段階的に強化できる
4. **十分な隔離レベル**: gVisor は Google GKE Autopilot のデフォルトランタイムとして採用実績あり
5. **起動速度**: Warm Pool 併用で実質的なレイテンシ増は最小限

## 2.2 全体アーキテクチャ

```
                          ┌─────────────────────────┐
                          │      API Gateway         │
                          │   (nginx / ALB)          │
                          └──────────┬──────────────┘
                                     │
                          ┌──────────▼──────────────┐
                          │     Backend Server       │
                          │  (API + Orchestrator)    │
                          │                          │
                          │  ┌────────────────────┐  │
                          │  │  SandboxManager    │  │
                          │  │  - acquire()       │  │
                          │  │  - release()       │  │
                          │  │  - WarmPool管理    │  │
                          │  └────────┬───────────┘  │
                          └───────────┼──────────────┘
                                      │ Docker API
                    ┌─────────────────┼─────────────────┐
                    │                 │                  │
           ┌────────▼───────┐ ┌──────▼────────┐ ┌──────▼────────┐
           │  Sandbox #1    │ │  Sandbox #2   │ │  Sandbox #N   │
           │  (Container)   │ │  (Container)  │ │  (Container)  │
           │                │ │               │ │               │
           │ ┌────────────┐ │ │               │ │               │
           │ │ Node.js    │ │ │  (idle -      │ │  (idle -      │
           │ │ Claude SDK │ │ │   Warm Pool)  │ │   Warm Pool)  │
           │ │            │ │ │               │ │               │
           │ │ cwd: /work │ │ │               │ │               │
           │ └────────────┘ │ │               │ │               │
           │                │ │               │ │               │
           │ Constraints:   │ │               │ │               │
           │ - seccomp      │ │               │ │               │
           │ - AppArmor     │ │               │ │               │
           │ - cgroups      │ │               │ │               │
           │ - read-only /  │ │               │ │               │
           │ - no network*  │ │               │ │               │
           └────────────────┘ └───────────────┘ └───────────────┘
                    │
              ┌─────┴─────┐
              │ OverlayFS │
              │ /work     │ ← S3 から同期されたファイル
              │ (writable)│
              └───────────┘
```

## 2.3 サンドボックスコンテナ設計

### コンテナイメージ (`Dockerfile.sandbox`)

```dockerfile
# サンドボックス専用イメージ
FROM python:3.11-slim AS sandbox-base

# Node.js (Claude Agent SDK 実行に必要)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Agent SDK のインストール
RUN npm install -g @anthropic-ai/claude-agent-sdk

# 基本的な開発ツール（エージェントが必要とする最小セット）
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# 非root ユーザー
RUN useradd -m -u 1000 -s /bin/bash sandbox
RUN mkdir -p /work && chown sandbox:sandbox /work

# pip の設定（グローバルインストール先を /work/.local に制限）
ENV PIP_TARGET=/work/.local/lib/python3.11/site-packages
ENV PYTHONPATH=/work/.local/lib/python3.11/site-packages
ENV PATH=/work/.local/bin:$PATH

USER sandbox
WORKDIR /work
```

### コンテナ起動パラメータ

```python
SANDBOX_CONTAINER_CONFIG = {
    # === リソース制限 ===
    "mem_limit": "2g",             # メモリ上限
    "memswap_limit": "2g",        # スワップ含む上限（=スワップ無効化）
    "cpu_period": 100000,          # CPU期間 (μs)
    "cpu_quota": 100000,           # CPU割当 (= 1コア)
    "pids_limit": 256,             # 最大プロセス数（fork bomb 防止）
    "storage_opt": {
        "size": "5G"               # ディスク上限
    },

    # === セキュリティ ===
    "read_only": True,             # ルートFS読み取り専用
    "security_opt": [
        "no-new-privileges",       # 特権昇格禁止
        "seccomp=sandbox-seccomp.json",
        "apparmor=sandbox-profile",
    ],
    "cap_drop": ["ALL"],           # 全Capability削除
    "cap_add": ["CHOWN", "SETUID", "SETGID"],  # pip install に必要な最小限
    "tmpfs": {
        "/tmp": "size=512m,noexec,nosuid,nodev",
        "/run": "size=64m,noexec,nosuid,nodev",
    },

    # === ネットワーク ===
    "network_mode": "none",        # デフォルトはネットワーク無効

    # === ファイルシステム ===
    # /work のみ書き込み可能（bind mount）
    "volumes": {
        # 実行時に動的に設定
        # "/host/path/workspace_{conv_id}": {"bind": "/work", "mode": "rw"}
    },
}
```

## 2.4 プロセス隔離

### 現行 vs 新設計

```
【現行】すべて backend コンテナ内
┌──────────────────────────────────────┐
│  Backend Container                    │
│  ┌─────────┐ ┌─────────┐ ┌────────┐ │
│  │ uvicorn │ │ SDK #1  │ │ SDK #2 │ │  ← 全プロセスが同一名前空間
│  │ (API)   │ │ (会話A)  │ │ (会話B) │ │
│  └─────────┘ └─────────┘ └────────┘ │
│  共有: PID空間, FS, Network, IPC     │
└──────────────────────────────────────┘

【新設計】SDK実行をサンドボックスコンテナに分離
┌──────────────────────┐
│  Backend Container    │    Docker API     ┌──────────────┐
│  ┌─────────────────┐ │◄──────────────────►│ Docker Daemon│
│  │ uvicorn (API)   │ │                    └──────┬───────┘
│  │ + Orchestrator  │ │                           │
│  └─────────────────┘ │               ┌───────────┼───────────┐
└──────────────────────┘               │           │           │
                                ┌──────▼──┐ ┌─────▼───┐ ┌─────▼───┐
                                │Sandbox#1│ │Sandbox#2│ │Sandbox#3│
                                │ (会話A)  │ │ (会話B)  │ │ (Warm)  │
                                │ 独立PID │ │ 独立PID │ │ 待機中  │
                                │ 独立FS  │ │ 独立FS  │ │         │
                                │ 独立Net │ │ 独立Net │ │         │
                                └─────────┘ └─────────┘ └─────────┘
```

### 通信設計

Backend ↔ Sandbox 間の通信:

```
Option A: Docker exec + stdio パイプ（推奨）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Backend                          Sandbox Container
  │                                    │
  │ docker exec -i sandbox_xxx         │
  │     node /usr/lib/claude-sdk/run   │
  │ ─────── stdin (JSON) ──────────►   │
  │ ◄────── stdout (JSON) ─────────    │
  │                                    │

利点:
  - 現行の stdio 通信モデルと完全互換
  - ClaudeSDKClient の変更が最小限
  - ネットワーク公開不要

Option B: Unix Domain Socket
━━━━━━━━━━━━━━━━━━━━━━━━━━

Backend                          Sandbox Container
  │                                    │
  │ /var/run/sandbox/{conv_id}.sock    │
  │ ◄─────── UDS ──────────►          │
  │                                    │

利点:
  - より柔軟な通信パターン
  - ネットワーク不要
欠点:
  - SDK側の変更が大きい
```

**推奨: Option A (Docker exec + stdio)**

理由: 現行の `ClaudeSDKClient` は stdin/stdout ベースの通信を前提としている。
Docker exec 経由にすることで、SDK 側の変更をゼロにできる。

## 2.5 ファイルシステム隔離

### レイヤー構造

```
┌──────────────────────────────────────────────────┐
│              Sandbox Container View               │
│                                                  │
│  / (read-only)         ← コンテナイメージ         │
│  ├── /usr/             ← Node.js, Python, 基本ツール│
│  ├── /home/sandbox/    ← ユーザーホーム            │
│  │                                               │
│  ├── /work/ (read-write) ← OverlayFS bind mount  │
│  │   ├── uploads/      ← S3から同期されたユーザーファイル│
│  │   ├── output/       ← エージェント出力         │
│  │   └── .local/       ← pip install 先          │
│  │                                               │
│  ├── /tmp/ (tmpfs, 512MB) ← 一時ファイル          │
│  └── /run/ (tmpfs, 64MB)  ← ランタイム            │
│                                                  │
│  アクセス不可:                                    │
│  ├── /var/lib/aiagent/  ← 他のワークスペース       │
│  ├── /skills/           ← スキルデータ            │
│  ├── /app/              ← バックエンドコード       │
│  └── Docker socket      ← コンテナ操作            │
└──────────────────────────────────────────────────┘
```

### OverlayFS 活用

```
# ベースレイヤー（読み取り専用、全サンドボックス共有）
/var/lib/sandbox/base/
  ├── .bashrc
  ├── .pip.conf          # pip設定（インストール先を /work/.local に固定）
  └── common-packages/   # 事前インストール済みパッケージ

# ワークスペースレイヤー（会話ごと、読み書き可能）
/var/lib/aiagent/workspaces/workspace_{conversation_id}/
  ├── uploads/           # S3から同期されたファイル
  ├── output/            # エージェント出力
  └── .local/            # この会話でpip installされたパッケージ

# マージビュー（サンドボックス内の /work）
overlay mount:
  lower = /var/lib/sandbox/base (ro)
  upper = /var/lib/aiagent/workspaces/workspace_{conv_id} (rw)
  merged = /work (サンドボックス内のビュー)
```

## 2.6 ネットワーク隔離

### ネットワークポリシー

```
┌──────────────────────────────────────────────────────┐
│                  Network Architecture                 │
│                                                      │
│  ┌────────────────┐      ┌─────────────────────────┐ │
│  │ Backend        │      │  Docker Network:         │ │
│  │ (bridge)       │      │  "sandbox-net" (internal)│ │
│  │                │      │                          │ │
│  │ ├── postgres   │      │  Sandbox は通常          │ │
│  │ ├── redis      │      │  network_mode: none      │ │
│  │ └── backend    │      │                          │ │
│  └────────────────┘      │  pip install 時のみ:     │ │
│                          │  一時的に sandbox-net に  │ │
│                          │  接続し、egress proxy     │ │
│                          │  経由でアクセス           │ │
│                          └─────────────────────────┘ │
│                                     │                 │
│                          ┌──────────▼──────────┐     │
│                          │  Egress Proxy        │     │
│                          │  (squid / envoy)     │     │
│                          │                      │     │
│                          │  許可リスト:          │     │
│                          │  - pypi.org          │     │
│                          │  - files.pythonhosted│     │
│                          │  - api.anthropic.com │     │
│                          │  - *.bedrock.*.      │     │
│                          │    amazonaws.com     │     │
│                          └──────────────────────┘     │
└──────────────────────────────────────────────────────┘
```

### ネットワークモード

| モード | 用途 | 設定 |
|--------|------|------|
| `none` | 通常のエージェント実行 | デフォルト。外部アクセス不可 |
| `egress-proxy` | pip install 等が必要な場合 | Proxy 経由で許可ドメインのみアクセス可 |

**注意**: Anthropic API (Bedrock) 呼び出しは Backend 側で行われるため、
Sandbox にネットワークアクセスは通常不要。

ただし、エージェントが `pip install` や `curl` を実行する場合は
一時的にネットワークアクセスを許可する必要がある。

### Egress Proxy の許可リスト

```yaml
# egress-proxy-allowlist.yaml
allowed_domains:
  # Python パッケージ
  - pypi.org
  - files.pythonhosted.org
  # Node.js パッケージ (必要な場合)
  - registry.npmjs.org
  # Anthropic API (Sandbox から直接呼ぶ場合)
  - api.anthropic.com
  # AWS Bedrock
  - "*.bedrock.*.amazonaws.com"
  - "*.bedrock-runtime.*.amazonaws.com"

blocked_domains:
  # メタデータエンドポイント（AWS認証情報窃取防止）
  - "169.254.169.254"
  - "fd00:ec2::254"
```

## 2.7 コンテナライフサイクル設計

### 設計方針: セッション固定コンテナモデル (Session-Sticky)

ユーザーは1つのチャット（会話）内で複数回メッセージを送受信する。
毎回コンテナを破棄・再作成するとレイテンシが発生し、`pip install` 等の
セッション内状態も失われてしまう。

そこで、**コンテナを会話に紐付けて維持**するモデルを採用する。

```
┌──────────────────────────────────────────────────────────────────┐
│            Session-Sticky Container Lifecycle                     │
│                                                                  │
│  Message 1 (新規会話)                                             │
│  ┌──────────┐     ┌──────────┐     ┌──────────────────┐         │
│  │Warm Pool │────►│Container │────►│ SDK Process #1   │         │
│  │ or 新規  │     │ 作成     │     │ 実行・完了       │         │
│  └──────────┘     └──────────┘     └──────────────────┘         │
│                        │                   │                     │
│                        │ bind: conv_id     │ SDK プロセス終了     │
│                        ▼                   ▼                     │
│                   ┌──────────────────────────────┐               │
│                   │ Container (alive, idle)       │               │
│                   │ - /work/ 状態保持             │               │
│                   │ - pip install 済パッケージ保持│               │
│                   │ - 生成ファイル保持            │               │
│                   └──────────────────────────────┘               │
│                        │                                         │
│  Message 2 (同一会話)   │ 既存コンテナを再利用                     │
│                        ▼                                         │
│                   ┌──────────────────┐                           │
│                   │ SDK Process #2   │  ← 同じコンテナで実行      │
│                   │ pip, ファイル等は│     起動レイテンシ≒0       │
│                   │ 前回から継続     │                            │
│                   └──────────────────┘                           │
│                        │                                         │
│                        ▼                                         │
│  ... (繰り返し) ...                                               │
│                        │                                         │
│  定期クリーンアップ      │ last_activity + TTL 超過                │
│                        ▼                                         │
│                   ┌──────────────────┐                           │
│                   │ S3最終同期       │                            │
│                   │ → Container破棄  │                            │
│                   │ → Local cleanup  │                            │
│                   └──────────────────┘                           │
└──────────────────────────────────────────────────────────────────┘
```

### コンテナ状態遷移

```
                    ┌─────────┐
                    │  WARM   │ ← Warm Pool 内（会話未紐付）
                    └────┬────┘
                         │ acquire(conversation_id)
                         ▼
                    ┌─────────┐
           ┌───────│ RUNNING │◄──────┐
           │       └────┬────┘       │
           │            │            │
           │  SDK完了   │            │ 次のメッセージ到着
           │            ▼            │
           │       ┌─────────┐       │
           │       │  IDLE   │───────┘
           │       └────┬────┘
           │            │ TTL超過 or 定期クリーンアップ
           │            ▼
           │    ┌──────────────┐
           └───►│  TERMINATED  │
                └──────────────┘
```

### S3 同期フローの変更

```
【旧設計】メッセージごとに完全サイクル
  Message N: S3→Local → Execute → Local→S3 → Local削除

【新設計】セッション固定
  Message 1: S3→Local → Execute → Local→S3 (ローカル維持)
  Message 2:            Execute → Local→S3 (ローカル維持)
  Message 3: S3→Local差分 → Execute → Local→S3 (ローカル維持)
  ...
  Cleanup:   最終S3同期 → Container破棄 → Local削除
```

**Message 3 の「S3→Local差分」について**: メッセージ間にユーザーが
フロントエンドからファイルをアップロードした場合、S3 に新規ファイルが
追加されている可能性がある。差分同期でこれを取り込む。

### Warm Pool

新規会話の初回レイテンシを削減するため、未割り当てコンテナのプールを維持する。

```
┌──────────────────────────────────────────────────────┐
│                    Warm Pool                          │
│                                                      │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐       │
│  │ Sandbox   │  │ Sandbox   │  │ Sandbox   │       │
│  │ (WARM)    │  │ (WARM)    │  │ (WARM)    │       │
│  └───────────┘  └───────────┘  └───────────┘       │
│                                                      │
│  新規会話到着時:                                       │
│    1. Pool からコンテナ取得 (<10ms)                    │
│    2. conversation_id を紐付                          │
│    3. ワークスペースを bind mount                      │
│    4. SDK プロセス起動                                │
│                                                      │
│  Pool が空の場合:                                     │
│    → オンデマンドでコンテナ作成 (~500ms)               │
│                                                      │
│  補充ルール:                                          │
│    pool_size < min_size → バックグラウンドで補充       │
└──────────────────────────────────────────────────────┘
```

```python
class ContainerLifecycleConfig:
    # Warm Pool
    warm_pool_min: int = 2           # 最小プール数
    warm_pool_max: int = 10          # 最大プール数
    warm_pool_target: int = 5        # 目標プール数

    # セッション固定
    session_idle_timeout: int = 86400   # 24時間 (最終活動からの TTL)
    session_max_lifetime: int = 172800  # 48時間 (コンテナ最大寿命)

    # クリーンアップ
    cleanup_schedule: str = "0 0 * * *"  # cron式: 毎日 0:00 UTC
    cleanup_stagger_seconds: int = 1800  # 30分間にわたって分散実行
    cleanup_grace_period: int = 300      # 5分のグレース期間

    # ヘルスチェック
    health_check_interval: int = 60     # 60秒間隔
    max_zombie_processes: int = 50      # ゾンビ上限（超過→不健全）
```

## 2.8 SDK 統合ポイント

### 変更が必要な箇所

```python
# 現行コード (execute_service.py:294)
async with ClaudeSDKClient(options=sdk_options) as client:
    await client.query(context.request.user_input)

# 新設計: セッション固定モデル
# 1. 既存コンテナがあれば再利用、なければ Warm Pool or 新規作成
sandbox = await sandbox_manager.acquire_or_reuse(
    conversation_id=context.conversation_id,
    workspace_path=cwd,
    env=options.get("env", {}),
    network_mode="none",
)
try:
    # 2. サンドボックス内で SDK を実行
    async with sandbox.execute_sdk(options=sdk_options) as client:
        await client.query(context.request.user_input)
        async for message in client.receive_response():
            # ...既存の処理...
finally:
    # 3. SDK プロセスのみ終了。コンテナは維持。
    await sandbox.mark_idle()
    # ※ sandbox_manager.release() は呼ばない（定期クリーンアップに委譲）
```

### SandboxManager インターフェース

```python
class SandboxManager:
    """サンドボックスライフサイクル管理 (セッション固定モデル)"""

    # --- 会話ごとのコンテナ管理 ---

    async def acquire_or_reuse(
        self,
        conversation_id: str,
        workspace_path: str,
        env: dict[str, str],
        network_mode: str = "none",
        resource_limits: ResourceLimits | None = None,
    ) -> Sandbox:
        """
        会話に紐付くコンテナを取得。

        1. 既存コンテナがあればヘルスチェック後に再利用
        2. なければ Warm Pool から取得
        3. Pool も空ならオンデマンド作成
        """

    async def release(self, sandbox: Sandbox) -> None:
        """コンテナを完全に破棄（定期クリーンアップから呼ばれる）"""

    async def discover_existing(self) -> dict[str, Sandbox]:
        """
        Docker labels から既存コンテナを検出。
        Backend 再起動後の復旧に使用。
        """

    # --- Warm Pool 管理 ---

    async def replenish_pool(self) -> None:
        """Warm Pool を目標サイズまで補充"""

    # --- 定期クリーンアップ ---

    async def cleanup_expired(self) -> CleanupReport:
        """
        TTL 超過コンテナを破棄。
        - RUNNING 状態のコンテナは絶対にスキップ
        - S3 最終同期 → コンテナ破棄 → ローカル削除
        """

    async def health_check_all(self) -> HealthReport:
        """全コンテナのヘルスチェック"""


class Sandbox:
    """個別サンドボックスの操作"""

    container_id: str
    conversation_id: str | None
    status: SandboxStatus          # warm | running | idle | terminated
    last_activity_at: datetime     # 最終活動時刻
    created_at: datetime           # コンテナ作成時刻

    async def execute_sdk(self, options: dict) -> ClaudeSDKClient:
        """サンドボックス内で SDK クライアントを起動"""

    async def mark_idle(self) -> None:
        """SDK 完了後、コンテナを IDLE 状態に遷移（破棄しない）"""

    async def is_healthy(self) -> bool:
        """コンテナの健全性チェック（ゾンビ、リソース、プロセス状態）"""

    async def final_sync_and_destroy(self) -> None:
        """最終 S3 同期後にコンテナとローカルファイルを破棄"""
```
