# ワークスペース隔離（サンドボックス）実装プラン

## 1. 現状分析

### 1.1 現在のアーキテクチャ

```
┌─────────────────────────────────────────────────────────┐
│          単一の Docker コンテナ (claude-multi-agent)       │
│                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ workspace_A  │  │ workspace_B  │  │ workspace_C  │    │
│  │ (tenant 1)   │  │ (tenant 2)   │  │ (tenant 1)   │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                 │             │
│         └─────── 共有 OS・共有プロセス空間 ──┘             │
│         └─────── 共有 Python 環境 ──────────┘             │
│         └─────── 共有ファイルシステム ───────┘             │
│                                                         │
│  Claude Agent SDK (claude CLI) が appuser として実行     │
│  permission_mode: "bypassPermissions"                   │
└─────────────────────────────────────────────────────────┘
```

### 1.2 現在の保護レイヤー

| レイヤー | 実装 | 安全性 |
|---------|------|--------|
| パストラバーサル防止 | `security.py:validate_path_traversal()` | APIレベルのみ。AI AgentのBashツールは制限なし |
| 相対パス指示 | システムプロンプトで「相対パスのみ使用」と指示 | AIが無視すれば突破可能。強制力なし |
| S3永続化 | 実行前後にS3↔ローカル同期 | ローカル実行中はホストFS上のディレクトリ |
| 非root実行 | `USER appuser` (UID 1000) | rootではないがコンテナ内の全リソースにアクセス可能 |
| クリーンアップ | `cleanup_local()` | 実行中の破壊的コマンドは防げない |

### 1.3 具体的なリスク

1. **`pip install` が共有環境を汚染** - 全テナントの全ワークスペースが同一Python環境を共有
2. **`rm -rf /app` 等の破壊的コマンド** - OSレベルの隔離がないため全体が停止する
3. **他テナントのワークスペースへのアクセス** - `ls /var/lib/aiagent/workspaces/` で他の会話ファイルが閲覧可能
4. **`permission_mode: "bypassPermissions"`** - Claude Agent SDKの安全チェックをバイパス
5. **プロセス空間の共有** - `kill`, `pkill` で他の実行を妨害可能
6. **ネットワークの共有** - 内部サービス（PostgreSQL, Redis）にアクセス可能

---

## 2. 目標アーキテクチャ

### 2.1 段階的隔離戦略（3段階）

| Tier | 技術 | 用途 | 起動時間 | 隔離強度 |
|------|------|------|----------|----------|
| Tier 1 (MVP) | Docker コンテナ | デフォルト全テナント | ~500ms | プロセスレベル（カーネル共有） |
| Tier 2 (強化) | Docker + gVisor (runsc) | 高セキュリティテナント | ~500ms | システムコール傍受 |
| Tier 3 (最大) | Firecracker microVM | コンプライアンス要件 | ~125ms | ハードウェア仮想化 |

### 2.2 目標アーキテクチャ図

```
┌──────────────────────────────────────────────────────────────┐
│                    ホストマシン                                │
│                                                              │
│  ┌─────────────────────────────────────────┐                │
│  │      バックエンドコンテナ (FastAPI)        │                │
│  │  - API処理                               │                │
│  │  - ワークスペース管理                      │                │
│  │  - S3同期                                │                │
│  │  - サンドボックスコンテナのオーケストレーション │               │
│  └────────────┬────────────────────────────┘                │
│               │ Docker Socket                                │
│               ▼                                              │
│  ┌────────────────────┐  ┌────────────────────┐            │
│  │  Sandbox Container  │  │  Sandbox Container  │            │
│  │  (Tenant A, Conv 1) │  │  (Tenant B, Conv 3) │            │
│  │                      │  │                      │            │
│  │  ┌────────────────┐ │  │  ┌────────────────┐ │            │
│  │  │ Claude Code CLI│ │  │  │ Claude Code CLI│ │            │
│  │  │ (Node.js)      │ │  │  │ (Node.js)      │ │            │
│  │  └────────────────┘ │  │  └────────────────┘ │            │
│  │                      │  │                      │            │
│  │  /workspace (mount)  │  │  /workspace (mount)  │            │
│  │  - 自分のファイルのみ  │  │  - 自分のファイルのみ  │            │
│  │                      │  │                      │            │
│  │  cap_drop: ALL       │  │  cap_drop: ALL       │            │
│  │  read_only rootfs    │  │  read_only rootfs    │            │
│  │  pids_limit: 256     │  │  pids_limit: 256     │            │
│  │  memory: 1GB         │  │  memory: 1GB         │            │
│  │  network: filtered   │  │  network: filtered   │            │
│  └────────────────────┘  └────────────────────────┘            │
│                                                              │
│  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐         │
│  │PostgreSQL│  │  Redis  │  │ Network Proxy       │         │
│  └─────────┘  └─────────┘  │ (*.amazonaws.comのみ) │         │
│                             └─────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 新規モジュール構成

```
app/
  services/
    sandbox/                          # NEW: サンドボックス隔離レイヤー
      __init__.py                     # 公開エクスポート
      base.py                         # SandboxProvider 抽象インタフェース
      docker_sandbox.py               # Docker ベース実装
      gvisor_sandbox.py               # gVisor 強化版
      sandbox_config.py               # リソース制限・セキュリティ設定
      sandbox_pool.py                 # プリウォームコンテナプール
      sandbox_metrics.py              # コンテナライフサイクルメトリクス
    execute/
      context.py                      # MODIFIED: SandboxConfig追加
      options_builder.py              # MODIFIED: サンドボックス対応
      sandbox_executor.py             # NEW: コンテナ内SDK実行
    execute_service.py                # MODIFIED: SandboxExecutor使用
    workspace_service.py              # MODIFIED: コンテナ対応
  config.py                           # MODIFIED: sandbox_* 設定追加
Dockerfile.sandbox                    # NEW: サンドボックスコンテナイメージ
docker-compose.yml                    # MODIFIED: Docker Socket マウント追加
```

---

## 4. コア設計

### 4.1 SandboxProvider（抽象ベース）

全隔離実装が準拠するインタフェース。

```python
# app/services/sandbox/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional


class IsolationTier(str, Enum):
    DOCKER = "docker"
    GVISOR = "gvisor"
    FIRECRACKER = "firecracker"


@dataclass
class SandboxResourceLimits:
    memory_mb: int = 1024           # メモリ上限 (MiB)
    memory_swap_mb: int = 1024      # メモリ+スワップ上限 (同値=スワップ無効)
    cpu_cores: float = 1.0          # CPU コア数
    cpu_period: int = 100000        # CPU スケジューリング期間 (μs)
    cpu_quota: int = 100000         # 期間あたりのCPUクォータ (μs)
    disk_size_mb: int = 5120        # ディスク上限 (MiB)
    pids_limit: int = 256           # 最大プロセス数
    timeout_seconds: int = 600      # 実行タイムアウト (10分)
    max_output_bytes: int = 10 * 1024 * 1024  # stdout/stderr上限 (10MB)


@dataclass
class SandboxNetworkPolicy:
    enabled: bool = False                # デフォルトでネットワーク無効
    allowed_domains: list[str] = field(default_factory=lambda: [
        "*.amazonaws.com",               # Bedrock エンドポイント
        "api.anthropic.com",             # 直接API フォールバック
    ])


@dataclass
class SandboxConfig:
    isolation_tier: IsolationTier = IsolationTier.DOCKER
    resources: SandboxResourceLimits = field(default_factory=SandboxResourceLimits)
    network: SandboxNetworkPolicy = field(default_factory=SandboxNetworkPolicy)
    read_only_rootfs: bool = True
    workspace_mount_path: str = "/workspace"
    sandbox_image: str = "aiagent-sandbox:latest"
    environment: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxInstance:
    sandbox_id: str
    container_id: str
    workspace_path: str
    status: str = "created"          # created | running | completed | failed | timeout
    exit_code: Optional[int] = None


class SandboxProvider(ABC):
    @abstractmethod
    async def create(self, config: SandboxConfig, workspace_host_path: str,
                     execution_script: str, environment: dict[str, str]) -> SandboxInstance:
        """サンドボックスコンテナ/VMを作成"""
        ...

    @abstractmethod
    async def start(self, instance: SandboxInstance) -> None:
        """サンドボックスの実行を開始"""
        ...

    @abstractmethod
    async def stream_output(self, instance: SandboxInstance) -> AsyncIterator[bytes]:
        """stdout/stderrをストリーム"""
        ...

    @abstractmethod
    async def wait(self, instance: SandboxInstance, timeout: int) -> int:
        """完了を待機し、終了コードを返す"""
        ...

    @abstractmethod
    async def destroy(self, instance: SandboxInstance) -> None:
        """サンドボックスを破棄しリソースをクリーンアップ"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """サンドボックスプロバイダの稼働状態を確認"""
        ...
```

### 4.2 DockerSandboxProvider（Tier 1 実装）

```python
# app/services/sandbox/docker_sandbox.py

import asyncio
import uuid
import time
from typing import AsyncIterator, Optional

import docker
import structlog

from app.services.sandbox.base import (
    SandboxConfig, SandboxInstance, SandboxProvider,
)

logger = structlog.get_logger(__name__)


class DockerSandboxProvider(SandboxProvider):
    """
    Dockerベースのサンドボックスプロバイダ。
    Docker SDK for Pythonを使用し、厳格なリソース制限と
    ネットワーク隔離を持つエフェメラルコンテナを作成。
    """

    def __init__(self, docker_base_url: Optional[str] = None):
        self._client = docker.DockerClient(
            base_url=docker_base_url or "unix:///var/run/docker.sock",
            timeout=30,
        )

    async def create(
        self, config: SandboxConfig, workspace_host_path: str,
        execution_script: str, environment: dict[str, str],
    ) -> SandboxInstance:
        sandbox_id = f"sandbox-{uuid.uuid4().hex[:12]}"

        container_kwargs = {
            "image": config.sandbox_image,
            "name": sandbox_id,
            "command": execution_script,
            "detach": True,
            "stdin_open": False,
            "tty": False,

            # リソース制限
            "mem_limit": f"{config.resources.memory_mb}m",
            "memswap_limit": f"{config.resources.memory_swap_mb}m",
            "cpu_period": config.resources.cpu_period,
            "cpu_quota": int(config.resources.cpu_cores * config.resources.cpu_period),
            "pids_limit": config.resources.pids_limit,

            # セキュリティ
            "read_only": config.read_only_rootfs,
            "cap_drop": ["ALL"],                          # 全Linux capabilityを削除
            "security_opt": ["no-new-privileges:true"],   # 権限昇格を防止

            # ファイルシステムマウント
            "volumes": {
                workspace_host_path: {
                    "bind": config.workspace_mount_path,
                    "mode": "rw",
                },
            },

            # 書き込み可能な一時ディレクトリ
            "tmpfs": {
                "/tmp": "size=256m,noexec",
                "/home/sandboxuser": "size=128m",
            },

            # 環境変数
            "environment": {
                **environment,
                "WORKSPACE_DIR": config.workspace_mount_path,
            },

            # 管理ラベル
            "labels": {
                "managed-by": "aiagent-sandbox",
                "sandbox-id": sandbox_id,
                **config.labels,
            },

            # ネットワーク
            "network_mode": "none" if not config.network.enabled else "bridge",
        }

        container = await asyncio.to_thread(
            self._client.containers.create, **container_kwargs
        )

        return SandboxInstance(
            sandbox_id=sandbox_id,
            container_id=container.id,
            workspace_path=workspace_host_path,
            status="created",
        )

    async def start(self, instance: SandboxInstance) -> None:
        container = await asyncio.to_thread(
            self._client.containers.get, instance.container_id
        )
        await asyncio.to_thread(container.start)
        instance.status = "running"

    async def stream_output(self, instance: SandboxInstance) -> AsyncIterator[bytes]:
        container = await asyncio.to_thread(
            self._client.containers.get, instance.container_id
        )
        log_stream = await asyncio.to_thread(
            container.logs, stream=True, follow=True, stdout=True, stderr=True
        )
        for chunk in log_stream:
            yield chunk

    async def wait(self, instance: SandboxInstance, timeout: int) -> int:
        container = await asyncio.to_thread(
            self._client.containers.get, instance.container_id
        )
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(container.wait), timeout=timeout,
            )
            exit_code = result.get("StatusCode", -1)
            instance.exit_code = exit_code
            instance.status = "completed" if exit_code == 0 else "failed"
            return exit_code
        except asyncio.TimeoutError:
            instance.status = "timeout"
            await asyncio.to_thread(container.kill)
            return -1

    async def destroy(self, instance: SandboxInstance) -> None:
        try:
            container = await asyncio.to_thread(
                self._client.containers.get, instance.container_id
            )
            await asyncio.to_thread(container.remove, force=True, v=True)
        except docker.errors.NotFound:
            pass

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(self._client.ping)
            return True
        except Exception:
            return False
```

### 4.3 SandboxExecutor（SDK実行のブリッジ）

現在のインプロセスSDK実行を、コンテナベース実行に置き換える最重要クラス。

```python
# app/services/execute/sandbox_executor.py

import asyncio
import json
import os
from typing import AsyncGenerator, Optional

import structlog

from app.config import get_settings
from app.services.sandbox.base import (
    IsolationTier, SandboxConfig, SandboxNetworkPolicy,
    SandboxProvider, SandboxResourceLimits,
)
from app.services.sandbox.docker_sandbox import DockerSandboxProvider
from app.services.execute.context import ExecutionContext

logger = structlog.get_logger(__name__)
settings = get_settings()


class SandboxExecutor:
    """
    Claude Agent SDKを隔離されたサンドボックスコンテナ内で実行。

    変更前: ClaudeSDKClient が FastAPIプロセス内で実行
    変更後: ClaudeSDKClient がエフェメラルDockerコンテナ内で実行

    実行フロー:
    1. ワークスペースディレクトリを準備（S3同期済み）
    2. SDKオプションをJSONファイルとしてワークスペースに書き出し
    3. サンドボックスコンテナを作成・起動
    4. コンテナのstdoutからNDJSONイベントをストリーム
    5. イベントをパースして呼び出し元にyield
    6. 完了後、ワークスペースをS3に同期
    7. サンドボックスコンテナを破棄
    """

    def __init__(self):
        self._providers: dict[IsolationTier, SandboxProvider] = {}
        self._initialize_providers()

    def _initialize_providers(self):
        try:
            self._providers[IsolationTier.DOCKER] = DockerSandboxProvider()
        except Exception as e:
            logger.error("Docker sandbox provider初期化失敗", error=str(e))

    async def execute(
        self, context: ExecutionContext, sdk_options: dict, workspace_path: str,
    ) -> AsyncGenerator[dict, None]:
        config = self._build_sandbox_config(context)
        provider = self._providers.get(config.isolation_tier)
        if not provider:
            raise RuntimeError("サンドボックスプロバイダが利用不可")

        # SDKオプションをワークスペースに書き出し
        options_dir = os.path.join(workspace_path, ".sandbox")
        os.makedirs(options_dir, exist_ok=True)
        with open(os.path.join(options_dir, "sdk_options.json"), "w") as f:
            json.dump(sdk_options, f)

        env_vars = self._build_sandbox_env(sdk_options)

        instance = await provider.create(
            config=config,
            workspace_host_path=workspace_path,
            execution_script="node /app/sandbox-agent-runner.js",
            environment=env_vars,
        )

        try:
            await provider.start(instance)

            async for chunk in provider.stream_output(instance):
                lines = chunk.decode("utf-8", errors="replace").strip().split("\n")
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("非JSONの出力", line=line[:200])

            await provider.wait(instance, config.resources.timeout_seconds)
        finally:
            await provider.destroy(instance)

    def _build_sandbox_config(self, context: ExecutionContext) -> SandboxConfig:
        return SandboxConfig(
            isolation_tier=IsolationTier(settings.sandbox_default_tier),
            resources=SandboxResourceLimits(
                memory_mb=settings.sandbox_memory_mb,
                cpu_cores=settings.sandbox_cpu_cores,
                timeout_seconds=settings.sandbox_timeout_seconds,
                pids_limit=settings.sandbox_pids_limit,
            ),
            network=SandboxNetworkPolicy(
                enabled=True,  # Bedrock API呼び出しにネットワークが必要
                allowed_domains=["*.amazonaws.com", "api.anthropic.com"],
            ),
            read_only_rootfs=True,
            sandbox_image=settings.sandbox_image,
            labels={
                "tenant-id": context.tenant_id,
                "conversation-id": context.conversation_id,
            },
        )

    def _build_sandbox_env(self, sdk_options: dict) -> dict[str, str]:
        env = {}
        if "env" in sdk_options:
            env.update(sdk_options["env"])
        return env
```

### 4.4 プリウォームコンテナプール

本番環境のパフォーマンス向上のため、事前作成済みコンテナのプール。

```python
# app/services/sandbox/sandbox_pool.py

import asyncio
from collections import deque
from typing import Optional

import structlog

from app.services.sandbox.base import SandboxConfig, SandboxInstance, SandboxProvider

logger = structlog.get_logger(__name__)


class SandboxPool:
    """
    プリウォームされたサンドボックスコンテナのプール。
    作成済みだが未起動のコンテナを維持し、
    コンテナ作成レイテンシをクリティカルパスから排除。
    """

    def __init__(self, provider: SandboxProvider, config: SandboxConfig,
                 pool_size: int = 5, refill_threshold: int = 2):
        self._provider = provider
        self._config = config
        self._pool_size = pool_size
        self._refill_threshold = refill_threshold
        self._pool: deque[SandboxInstance] = deque()
        self._lock = asyncio.Lock()
        self._refill_task: Optional[asyncio.Task] = None

    async def initialize(self):
        await self._refill()

    async def acquire(self, workspace_host_path: str,
                      execution_script: str, environment: dict[str, str]) -> SandboxInstance:
        async with self._lock:
            if self._pool:
                instance = self._pool.popleft()
            else:
                instance = await self._provider.create(
                    self._config, workspace_host_path, execution_script, environment,
                )
            if len(self._pool) < self._refill_threshold:
                self._schedule_refill()
            return instance

    async def _refill(self):
        while len(self._pool) < self._pool_size:
            try:
                instance = await self._provider.create(
                    config=self._config,
                    workspace_host_path="/tmp/pool-placeholder",
                    execution_script="sleep infinity",
                    environment={},
                )
                self._pool.append(instance)
            except Exception as e:
                logger.error("プリウォーム失敗", error=str(e))
                break

    def _schedule_refill(self):
        if self._refill_task is None or self._refill_task.done():
            self._refill_task = asyncio.create_task(self._refill())

    async def shutdown(self):
        if self._refill_task and not self._refill_task.done():
            self._refill_task.cancel()
        for instance in self._pool:
            try:
                await self._provider.destroy(instance)
            except Exception:
                pass
        self._pool.clear()
```

### 4.5 サンドボックスコンテナイメージ

```dockerfile
# Dockerfile.sandbox
# Claude Agent SDK 実行用の軽量サンドボックスイメージ

FROM node:22-alpine AS sandbox

# Claude Code CLI をインストール
RUN npm install -g @anthropic-ai/claude-code

# 非rootユーザーを作成
RUN adduser -D -u 1000 sandboxuser

# ワークスペースマウントポイントを作成
RUN mkdir -p /workspace && chown sandboxuser:sandboxuser /workspace

# エージェントランナースクリプトをコピー
COPY sandbox-agent-runner.js /app/sandbox-agent-runner.js

# 非rootユーザーに切り替え
USER sandboxuser

WORKDIR /workspace

ENTRYPOINT ["node", "/app/sandbox-agent-runner.js"]
```

---

## 5. 既存ファイルの変更

### 5.1 設定追加 (`app/config.py`)

```python
# ============================================
# サンドボックス隔離設定
# ============================================
sandbox_enabled: bool = False                    # フィーチャーフラグ（段階的ロールアウト）
sandbox_default_tier: str = "docker"             # docker | gvisor | firecracker
sandbox_image: str = "aiagent-sandbox:latest"    # サンドボックスコンテナイメージ
sandbox_memory_mb: int = 1024                    # デフォルトメモリ上限 (MiB)
sandbox_cpu_cores: float = 1.0                   # デフォルトCPUコア数
sandbox_timeout_seconds: int = 600               # デフォルト実行タイムアウト
sandbox_pids_limit: int = 256                    # デフォルトPID制限
sandbox_pool_size: int = 5                       # プリウォームプールサイズ
sandbox_pool_refill_threshold: int = 2           # このカウント以下でリフィル
sandbox_gvisor_enabled: bool = False             # gVisor Tier 有効化
sandbox_docker_socket: str = "unix:///var/run/docker.sock"
```

### 5.2 ExecuteService の変更 (`app/services/execute_service.py`)

`_execute_with_sdk` メソッドで、フィーチャーフラグに基づいて分岐:

```python
async def _execute_with_sdk(self, context, options, tool_tracker, seq_counter):
    if settings.sandbox_enabled:
        # 新パス: サンドボックスコンテナ内で実行
        sandbox_executor = SandboxExecutor()
        async for event in sandbox_executor.execute(
            context=context,
            sdk_options=options,
            workspace_path=context.cwd,
        ):
            yield self._convert_sandbox_event(event, context, tool_tracker, seq_counter)
    else:
        # レガシーパス: インプロセスSDK実行（既存コード変更なし）
        ...
```

### 5.3 docker-compose.yml の変更

```yaml
backend:
  volumes:
    - ./app:/app/app
    - skills_data:/skills
    - workspaces_data:/var/lib/aiagent/workspaces
    - /var/run/docker.sock:/var/run/docker.sock  # NEW: Docker Socket アクセス
```

---

## 6. ワークスペースライフサイクルの変更

```
変更前:
  S3 → sync_to_local(/var/lib/aiagent/workspaces/workspace_{id})
  → SDK がそのディレクトリで直接読み書き
  → sync_from_local → S3
  → cleanup_local

変更後 (サンドボックス):
  S3 → sync_to_local(/var/lib/aiagent/workspaces/workspace_{id})  [ホスト上]
  → ディレクトリをコンテナの /workspace としてマウント
  → コンテナ内の SDK が /workspace を読み書き（自分のマウントのみ見える）
  → コンテナ終了
  → sync_from_local (ホストが同じディレクトリを読み取り) → S3
  → cleanup_local
  → コンテナ破棄
```

WorkspaceServiceの同期コードは変更不要。ホスト側のディレクトリパスは同一のまま、
bind mount によってファイルシステム隔離を実現する。

---

## 7. ネットワーク隔離

### 推奨: プロキシベースのドメインフィルタリング

```
サンドボックスコンテナ → Docker ネットワーク → Proxy コンテナ → インターネット
                                              (*.amazonaws.com のみ許可)
```

- Squid や専用Goプロキシを使用
- `*.amazonaws.com` と `api.anthropic.com` のみ通過
- その他の外部通信は全てブロック
- PostgreSQL, Redis への内部アクセスも不可

---

## 8. セキュリティ強化詳細

### コンテナ内のセキュリティ設定

| 設定 | 値 | 効果 |
|------|------|------|
| `cap_drop` | `["ALL"]` | 全Linux capabilityを削除 |
| `security_opt` | `["no-new-privileges:true"]` | 権限昇格を防止 |
| `read_only` | `true` | rootfsを読み取り専用に |
| `tmpfs` | `/tmp (256m, noexec)` | 一時ファイル用（実行不可） |
| `pids_limit` | `256` | fork bomb防止 |
| `mem_limit` | `1024m` | メモリOOM防止 |
| `network_mode` | フィルタリング済み | 不正通信を防止 |

### `bypassPermissions` について

コンテナ内では `bypassPermissions` を維持する。これは安全である理由:
- コンテナ境界がセキュリティ強制レイヤーとなる
- SDKの権限モードに関係なく、マウントポイント外にはアクセス不可
- [Anthropic公式ドキュメント](https://code.claude.com/docs/en/sandboxing)の推奨パターンに準拠

---

## 9. 実装フェーズ

### Phase 1: Docker サンドボックス MVP

1. `Dockerfile.sandbox` と `sandbox-agent-runner.js` を作成
2. `SandboxProvider` インタフェースと `DockerSandboxProvider` を実装
3. `SandboxExecutor` をNDJSONイベントパーシング付きで実装
4. `sandbox_enabled` フィーチャーフラグを `Settings` に追加
5. `ExecuteService._execute_with_sdk()` を分岐対応に変更
6. `docker-compose.yml` にDocker Socketマウントを追加
7. サンドボックスイメージをビルド・テスト

**検証項目:**
- [ ] エージェントが `/workspace` 外のファイルを読めないこと
- [ ] 未許可ホストへのネットワーク接続が不可なこと
- [ ] 実行後にコンテナが確実に破棄されること
- [ ] リソース制限が強制されること（メモリOOM、CPU制限、PID制限）
- [ ] `pip install` がホスト環境に影響しないこと
- [ ] `rm -rf /` がホストに影響しないこと

### Phase 2: 本番ハードニング

1. `SandboxPool` でプリウォームコンテナを実装
2. コンテナライフサイクルメトリクスを追加
3. ヘルスチェックエンドポイントを追加
4. ネットワークプロキシでドメインフィルタリングを実装
5. テナントレベルのサンドボックス設定オーバーライドを追加
6. 孤児コンテナリーパー（バックグラウンドタスク）を追加
7. `app/main.py` のlifespanハンドラでプール初期化/シャットダウン

### Phase 3: gVisor 強化

1. `GVisorSandboxProvider` を実装
2. gVisor runtime をデプロイインフラにインストール
3. テナント毎の隔離Tier設定を追加
4. システムコールフィルタリングをテスト

### Phase 4: 監視・可観測性

1. サンドボックスライフサイクルの構造化ログを追加
2. 既存Prometheusメトリクスとの統合
3. アラート設定: コンテナ作成失敗、タイムアウト率、リソース制限到達、プール枯渇
4. コンテナコスト追跡

---

## 10. リスクと緩和策

| リスク | 緩和策 |
|--------|--------|
| Docker Socket アクセスによるホスト権限 | docker-socket-proxy で create/start/stop/remove/logs のみ許可 |
| 共有カーネルによるコンテナエスケープ (Tier 1) | Tier 2 (gVisor) にアップグレード。Tier 1 では `cap_drop=ALL`, `no-new-privileges`, read-only rootfs で緩和 |
| クラッシュによる孤児コンテナ | `managed-by=aiagent-sandbox` ラベルで定期スキャンし、タイムアウト超過コンテナを強制削除 |
| コンテナ作成のパフォーマンスオーバーヘッド | プリウォームプール（~500ms→ほぼゼロ）。SDK実行自体が数秒〜数分のため許容範囲 |

---

## 11. 設計判断のまとめ

| 判断 | 選択 | 理由 |
|------|------|------|
| ネスト vs サイドカー | サイドカーコンテナ | Docker-in-Dockerの複雑さとセキュリティリスクを回避 |
| イベント通信方式 | NDJSON over stdout | 追加プロトコル不要。既存SDKの出力形式に合致 |
| コンテナ内ランタイム | Node.js (直接) | Claude Code CLIがNode.jsベース。Python SDK wrapperの1層を削減 |
| `bypassPermissions` | コンテナ内で維持 | コンテナ境界がセキュリティレイヤー。Anthropic公式推奨パターン |
| ロールアウト戦略 | フィーチャーフラグ | 両パスの並行運用と容易なロールバックを実現 |

---

## 参考資料

- [Anthropic Agent SDK Hosting Guide](https://platform.claude.com/docs/en/agent-sdk/hosting)
- [Claude Code Sandboxing Docs](https://code.claude.com/docs/en/sandboxing)
- [Anthropic Engineering - Claude Code Sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing)
- [Docker SDK for Python](https://docker-py.readthedocs.io/en/stable/containers.html)
- [gVisor](https://gvisor.dev/)
- [Firecracker MicroVMs](https://firecracker-microvm.github.io/)
- [Kubernetes Agent Sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
- [TextCortex Claude Code Sandbox](https://github.com/textcortex/claude-code-sandbox)
