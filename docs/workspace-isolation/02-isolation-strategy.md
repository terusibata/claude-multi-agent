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

## 2.7 Warm Pool 設計

### 目的

コンテナ起動のレイテンシ（~500ms）をユーザー体験から排除する。

```
┌──────────────────────────────────────────────────────┐
│                    Warm Pool                          │
│                                                      │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐       │
│  │ Sandbox   │  │ Sandbox   │  │ Sandbox   │       │
│  │ (idle)    │  │ (idle)    │  │ (idle)    │       │
│  │ ready=true│  │ ready=true│  │ ready=true│       │
│  └───────────┘  └───────────┘  └───────────┘       │
│                                                      │
│  Pool Size: min=2, max=10, target=5                  │
│  TTL: 300s (idle timeout)                            │
│  Health Check: 30s interval                          │
└──────────────────────────────────────────────────────┘

リクエスト到着時:
  1. Warm Pool から idle コンテナを取得 (<10ms)
  2. ワークスペースディレクトリを bind mount
  3. 環境変数を設定
  4. SDK プロセスを起動

実行完了後:
  1. SDK プロセスを終了
  2. ワークスペースディレクトリを unmount
  3. /work をクリーンアップ
  4. Pool に返却 or 破棄して新規作成
```

### Warm Pool 管理

```python
class WarmPoolConfig:
    min_size: int = 2          # 最小プール数
    max_size: int = 10         # 最大プール数
    target_size: int = 5       # 目標プール数
    idle_timeout: int = 300    # アイドルタイムアウト (秒)
    max_lifetime: int = 3600   # コンテナ最大寿命 (秒)
    health_check_interval: int = 30  # ヘルスチェック間隔 (秒)
```

## 2.8 SDK 統合ポイント

### 変更が必要な箇所

```python
# 現行コード (execute_service.py:294)
async with ClaudeSDKClient(options=sdk_options) as client:
    await client.query(context.request.user_input)

# 新設計
async with SandboxManager() as sandbox_mgr:
    sandbox = await sandbox_mgr.acquire(
        conversation_id=context.conversation_id,
        workspace_path=cwd,
        env=options.get("env", {}),
        network_mode="none",  # or "egress-proxy"
    )
    try:
        # サンドボックス内で SDK を実行
        async with sandbox.execute_sdk(options=sdk_options) as client:
            await client.query(context.request.user_input)
            async for message in client.receive_response():
                # ...既存の処理...
    finally:
        await sandbox_mgr.release(sandbox)
```

### SandboxManager インターフェース

```python
class SandboxManager:
    """サンドボックスライフサイクル管理"""

    async def acquire(
        self,
        conversation_id: str,
        workspace_path: str,
        env: dict[str, str],
        network_mode: str = "none",
        resource_limits: ResourceLimits | None = None,
    ) -> Sandbox:
        """Warm Pool からサンドボックスを取得、または新規作成"""

    async def release(self, sandbox: Sandbox) -> None:
        """サンドボックスを解放（クリーンアップ後 Pool に返却 or 破棄）"""

    async def health_check(self) -> PoolStatus:
        """プール状態のヘルスチェック"""


class Sandbox:
    """個別サンドボックスの操作"""

    container_id: str
    conversation_id: str
    status: SandboxStatus  # idle | running | error

    async def execute_sdk(self, options: dict) -> ClaudeSDKClient:
        """サンドボックス内で SDK クライアントを起動"""

    async def cleanup(self) -> None:
        """ワークスペースをクリーンアップしてリセット"""
```
