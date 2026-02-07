# 4. 実装計画

## 4.1 フェーズ概要

```
Phase 1: コンテナ隔離の基盤構築         (2-3 weeks)
  │  サンドボックスコンテナ + Docker API 統合
  │
Phase 2: セキュリティ強化               (1-2 weeks)
  │  seccomp + AppArmor + ネットワーク隔離
  │
Phase 3: Warm Pool + パフォーマンス最適化 (1-2 weeks)
  │  プール管理 + 事前起動 + 監視
  │
Phase 4: gVisor 統合 (Optional)          (1 week)
     カーネル隔離の強化
```

## 4.2 Phase 1: コンテナ隔離の基盤構築

### 目標

- エージェント実行を専用コンテナで行う基本フローの確立
- 既存の S3 ワークスペースフローとの統合

### 4.2.1 サンドボックスイメージの作成

**新規ファイル**: `Dockerfile.sandbox`

```dockerfile
FROM python:3.11-slim AS sandbox

# Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Agent SDK
RUN npm install -g @anthropic-ai/claude-agent-sdk

# 基本ツール
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl jq \
    && rm -rf /var/lib/apt/lists/*

# よく使われる Python パッケージを事前インストール
COPY requirements-sandbox.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-sandbox.txt \
    && rm /tmp/requirements-sandbox.txt

# 非root ユーザー
RUN useradd -m -u 1000 -s /bin/bash sandbox \
    && mkdir -p /work \
    && chown sandbox:sandbox /work

# pip 設定
COPY sandbox-pip.conf /home/sandbox/.pip/pip.conf
RUN chown -R sandbox:sandbox /home/sandbox/.pip

USER sandbox
WORKDIR /work

# ヘルスチェック用
HEALTHCHECK --interval=30s --timeout=5s \
    CMD test -f /tmp/.sandbox-ready || exit 1

# エントリーポイント: ready マーカーを作成して待機
CMD ["sh", "-c", "touch /tmp/.sandbox-ready && sleep infinity"]
```

### 4.2.2 SandboxManager の実装

**新規ファイル**: `app/services/sandbox/manager.py`

```python
"""
サンドボックス管理サービス

Docker API を使用してエージェント実行用のサンドボックスコンテナを管理する。
"""
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import docker
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class SandboxStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"
    TERMINATED = "terminated"


@dataclass
class ResourceLimits:
    """サンドボックスのリソース制限"""
    mem_limit: str = "2g"
    cpu_quota: int = 100000  # 1 core
    cpu_period: int = 100000
    pids_limit: int = 256
    storage_limit: str = "5g"


@dataclass
class Sandbox:
    """サンドボックスインスタンス"""
    container_id: str
    conversation_id: Optional[str] = None
    status: SandboxStatus = SandboxStatus.IDLE
    workspace_path: Optional[str] = None


class SandboxManager:
    """
    サンドボックスライフサイクル管理

    Docker コンテナの作成・取得・解放を管理する。
    """

    def __init__(self):
        self.docker_client = docker.DockerClient.from_env()
        self.image_name = "ai-agent-sandbox:latest"

    async def acquire(
        self,
        conversation_id: str,
        workspace_path: str,
        env: dict[str, str],
        network_mode: str = "none",
        resource_limits: Optional[ResourceLimits] = None,
    ) -> Sandbox:
        """サンドボックスを取得または作成"""
        limits = resource_limits or ResourceLimits()

        container = await asyncio.to_thread(
            self._create_container,
            conversation_id=conversation_id,
            workspace_path=workspace_path,
            env=env,
            network_mode=network_mode,
            limits=limits,
        )

        sandbox = Sandbox(
            container_id=container.id,
            conversation_id=conversation_id,
            status=SandboxStatus.RUNNING,
            workspace_path=workspace_path,
        )

        logger.info(
            "サンドボックス取得",
            container_id=container.short_id,
            conversation_id=conversation_id,
        )

        return sandbox

    async def release(self, sandbox: Sandbox) -> None:
        """サンドボックスを解放"""
        try:
            container = self.docker_client.containers.get(sandbox.container_id)
            await asyncio.to_thread(container.stop, timeout=10)
            await asyncio.to_thread(container.remove, force=True)

            logger.info(
                "サンドボックス解放",
                container_id=sandbox.container_id[:12],
                conversation_id=sandbox.conversation_id,
            )
        except docker.errors.NotFound:
            logger.warning(
                "サンドボックスが既に存在しない",
                container_id=sandbox.container_id[:12],
            )
        except Exception as e:
            logger.error(
                "サンドボックス解放エラー",
                error=str(e),
                container_id=sandbox.container_id[:12],
            )

    def _create_container(
        self,
        conversation_id: str,
        workspace_path: str,
        env: dict[str, str],
        network_mode: str,
        limits: ResourceLimits,
    ):
        """Docker コンテナを作成"""
        return self.docker_client.containers.run(
            self.image_name,
            detach=True,
            name=f"sandbox-{conversation_id[:8]}",
            environment=env,
            volumes={
                workspace_path: {"bind": "/work", "mode": "rw"},
            },
            mem_limit=limits.mem_limit,
            memswap_limit=limits.mem_limit,
            cpu_period=limits.cpu_period,
            cpu_quota=limits.cpu_quota,
            pids_limit=limits.pids_limit,
            network_mode=network_mode,
            read_only=True,
            security_opt=[
                "no-new-privileges",
            ],
            cap_drop=["ALL"],
            cap_add=["CHOWN", "SETUID", "SETGID"],
            tmpfs={
                "/tmp": "size=512m,noexec,nosuid,nodev",
                "/run": "size=64m,noexec,nosuid,nodev",
            },
            labels={
                "ai-agent.role": "sandbox",
                "ai-agent.conversation-id": conversation_id,
            },
            auto_remove=False,
        )
```

### 4.2.3 SDK 統合アダプター

**新規ファイル**: `app/services/sandbox/sdk_adapter.py`

```python
"""
サンドボックス内 SDK 実行アダプター

Docker exec を使用してサンドボックスコンテナ内で
Claude Agent SDK を実行するアダプター。
"""
import asyncio
import json
from typing import AsyncGenerator

import docker
import structlog

from app.services.sandbox.manager import Sandbox

logger = structlog.get_logger(__name__)


class SandboxSDKClient:
    """
    サンドボックス内で Claude SDK を実行するクライアント

    Docker exec を使用して stdin/stdout 通信を行う。
    既存の ClaudeSDKClient と同じインターフェースを提供。
    """

    def __init__(self, sandbox: Sandbox, options: dict):
        self.sandbox = sandbox
        self.options = options
        self.docker_client = docker.DockerClient.from_env()
        self._exec_id: str | None = None
        self._socket = None

    async def __aenter__(self):
        """SDK プロセスを起動"""
        options_json = json.dumps(self.options)

        # docker exec でSDKプロセスを起動
        exec_instance = await asyncio.to_thread(
            self.docker_client.api.exec_create,
            self.sandbox.container_id,
            cmd=["node", "-e", f"""
                const {{ ClaudeSDKClient }} = require('@anthropic-ai/claude-agent-sdk');
                // stdin/stdout ベースの通信ブリッジ
                // options は環境変数から取得
                process.stdin.resume();
                // ... SDK 実行ロジック
            """],
            stdin=True,
            stdout=True,
            stderr=True,
            user="sandbox",
            workdir="/work",
        )
        self._exec_id = exec_instance["Id"]

        # exec start で socket を取得
        self._socket = await asyncio.to_thread(
            self.docker_client.api.exec_start,
            self._exec_id,
            socket=True,
        )

        return self

    async def __aexit__(self, *args):
        """クリーンアップ"""
        if self._socket:
            self._socket.close()

    async def query(self, user_input: str) -> None:
        """ユーザー入力を送信"""
        message = json.dumps({"type": "query", "input": user_input})
        # socket 経由で stdin に書き込み
        await asyncio.to_thread(
            self._socket._sock.sendall,
            (message + "\n").encode()
        )

    async def receive_response(self) -> AsyncGenerator:
        """レスポンスをストリーミング受信"""
        buffer = b""
        while True:
            data = await asyncio.to_thread(
                self._socket._sock.recv, 4096
            )
            if not data:
                break

            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    message = json.loads(line.decode())
                    yield message
```

### 4.2.4 ExecuteService の変更

**変更ファイル**: `app/services/execute_service.py`

主要な変更点:

```python
# 変更前 (Line 294)
async with ClaudeSDKClient(options=sdk_options) as client:
    await client.query(context.request.user_input)
    async for message in client.receive_response():
        ...

# 変更後
sandbox_manager = SandboxManager()
sandbox = await sandbox_manager.acquire(
    conversation_id=context.conversation_id,
    workspace_path=cwd,
    env=options.get("env", {}),
    network_mode="none",
)
try:
    async with ClaudeSDKClient(options=sdk_options) as client:
        # SDK の cwd はサンドボックス内の /work を指す
        await client.query(context.request.user_input)
        async for message in client.receive_response():
            ...
finally:
    await sandbox_manager.release(sandbox)
```

### 4.2.5 docker-compose.yml の変更

```yaml
services:
  backend:
    # ... 既存設定 ...
    volumes:
      - ./app:/app/app
      - skills_data:/skills
      - workspaces_data:/var/lib/aiagent/workspaces
      - /var/run/docker.sock:/var/run/docker.sock  # Docker API アクセス
    environment:
      SANDBOX_ENABLED: "true"
      SANDBOX_IMAGE: "ai-agent-sandbox:latest"
```

### 4.2.6 Phase 1 変更ファイル一覧

| ファイル | 操作 | 説明 |
|---------|------|------|
| `Dockerfile.sandbox` | 新規 | サンドボックスイメージ |
| `requirements-sandbox.txt` | 新規 | 事前インストールパッケージ |
| `sandbox-pip.conf` | 新規 | pip 設定 |
| `app/services/sandbox/__init__.py` | 新規 | パッケージ初期化 |
| `app/services/sandbox/manager.py` | 新規 | SandboxManager |
| `app/services/sandbox/sdk_adapter.py` | 新規 | SDK アダプター |
| `app/services/sandbox/config.py` | 新規 | サンドボックス設定 |
| `app/services/execute_service.py` | 変更 | SDK 実行部分の変更 |
| `app/services/execute/options_builder.py` | 変更 | cwd の決定ロジック変更 |
| `app/config.py` | 変更 | サンドボックス設定項目追加 |
| `docker-compose.yml` | 変更 | Docker socket マウント追加 |
| `requirements.txt` | 変更 | `docker` パッケージ追加 |

## 4.3 Phase 2: セキュリティ強化

### 目標

- seccomp / AppArmor プロファイルの適用
- ネットワーク隔離と Egress Proxy の導入
- 環境変数の最小化と一時認証

### 4.3.1 追加ファイル

| ファイル | 説明 |
|---------|------|
| `sandbox/seccomp/sandbox-seccomp.json` | seccomp プロファイル |
| `sandbox/apparmor/sandbox-profile` | AppArmor プロファイル |
| `sandbox/egress-proxy/squid.conf` | Egress Proxy 設定 |
| `sandbox/egress-proxy/allowlist.txt` | 許可ドメインリスト |
| `app/services/sandbox/network.py` | ネットワークモード管理 |
| `app/services/sandbox/credentials.py` | 一時認証情報管理 |

### 4.3.2 Egress Proxy (Squid)

```yaml
# docker-compose.yml に追加
services:
  egress-proxy:
    image: ubuntu/squid:latest
    container_name: ai-agent-egress-proxy
    volumes:
      - ./sandbox/egress-proxy/squid.conf:/etc/squid/squid.conf:ro
      - ./sandbox/egress-proxy/allowlist.txt:/etc/squid/allowlist.txt:ro
    networks:
      - sandbox-net
    restart: unless-stopped

networks:
  sandbox-net:
    internal: true  # 外部への直接アクセスは不可
```

### 4.3.3 seccomp / AppArmor の適用

```python
# manager.py の _create_container を更新
security_opt = [
    "no-new-privileges",
    f"seccomp={seccomp_profile_path}",
    f"apparmor=sandbox-profile",
]
```

## 4.4 Phase 3: Warm Pool + パフォーマンス最適化

### 目標

- Warm Pool によるレイテンシ削減
- リソース効率の最適化
- 監視・アラート基盤

### 4.4.1 WarmPool 実装

**新規ファイル**: `app/services/sandbox/warm_pool.py`

```python
"""
Warm Pool 管理

事前にサンドボックスコンテナを起動しておき、
リクエスト到着時に即座に割り当てることで
コンテナ起動のレイテンシを排除する。
"""
import asyncio
from collections import deque
from dataclasses import dataclass

import structlog

from app.services.sandbox.manager import Sandbox, SandboxManager, SandboxStatus

logger = structlog.get_logger(__name__)


@dataclass
class WarmPoolConfig:
    min_size: int = 2
    max_size: int = 10
    target_size: int = 5
    idle_timeout_seconds: int = 300
    max_lifetime_seconds: int = 3600
    health_check_interval_seconds: int = 30
    replenish_interval_seconds: int = 10


class WarmPool:
    """
    サンドボックス Warm Pool

    idle 状態のサンドボックスをプールし、
    リクエスト到着時に即座に提供する。
    """

    def __init__(
        self,
        manager: SandboxManager,
        config: WarmPoolConfig | None = None,
    ):
        self.manager = manager
        self.config = config or WarmPoolConfig()
        self._pool: deque[Sandbox] = deque()
        self._active: dict[str, Sandbox] = {}  # conversation_id -> Sandbox
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Warm Pool を起動"""
        self._running = True
        self._tasks.append(
            asyncio.create_task(self._replenish_loop())
        )
        self._tasks.append(
            asyncio.create_task(self._health_check_loop())
        )
        # 初期プールを作成
        await self._replenish()
        logger.info("Warm Pool 起動完了", target_size=self.config.target_size)

    async def stop(self) -> None:
        """Warm Pool を停止"""
        self._running = False
        for task in self._tasks:
            task.cancel()
        # アクティブなサンドボックスを解放
        for sandbox in list(self._active.values()):
            await self.manager.release(sandbox)
        # プール内のサンドボックスを解放
        while self._pool:
            sandbox = self._pool.popleft()
            await self.manager.release(sandbox)
        logger.info("Warm Pool 停止完了")

    async def acquire(self, conversation_id: str, **kwargs) -> Sandbox:
        """プールからサンドボックスを取得"""
        if self._pool:
            sandbox = self._pool.popleft()
            sandbox.conversation_id = conversation_id
            sandbox.status = SandboxStatus.RUNNING
            # ワークスペースをバインド（動的マウント）
            # ...
        else:
            # プールが空の場合はオンデマンド作成
            logger.warning("Warm Pool が空: オンデマンド作成")
            sandbox = await self.manager.acquire(
                conversation_id=conversation_id, **kwargs
            )

        self._active[conversation_id] = sandbox
        return sandbox

    async def release(self, sandbox: Sandbox) -> None:
        """サンドボックスをプールに返却"""
        self._active.pop(sandbox.conversation_id, None)
        sandbox.conversation_id = None
        sandbox.status = SandboxStatus.IDLE

        if len(self._pool) < self.config.max_size:
            # クリーンアップしてプールに返却
            # (ワークスペースをunmount、/work を初期化)
            self._pool.append(sandbox)
        else:
            # プールが満杯なら破棄
            await self.manager.release(sandbox)

    async def _replenish(self) -> None:
        """プールを補充"""
        needed = self.config.target_size - len(self._pool)
        for _ in range(max(0, needed)):
            try:
                sandbox = await self.manager.acquire(
                    conversation_id="warmpool-idle",
                    workspace_path="/tmp/warmpool-placeholder",
                    env={},
                )
                sandbox.status = SandboxStatus.IDLE
                self._pool.append(sandbox)
            except Exception as e:
                logger.error("Warm Pool 補充エラー", error=str(e))
                break

    async def _replenish_loop(self) -> None:
        """定期的にプールを補充"""
        while self._running:
            await asyncio.sleep(self.config.replenish_interval_seconds)
            if len(self._pool) < self.config.min_size:
                await self._replenish()

    async def _health_check_loop(self) -> None:
        """定期的にヘルスチェック"""
        while self._running:
            await asyncio.sleep(self.config.health_check_interval_seconds)
            # 不健全なコンテナを検出して置換
            healthy_pool: deque[Sandbox] = deque()
            for sandbox in self._pool:
                if await self._is_healthy(sandbox):
                    healthy_pool.append(sandbox)
                else:
                    await self.manager.release(sandbox)
            self._pool = healthy_pool

    async def _is_healthy(self, sandbox: Sandbox) -> bool:
        """サンドボックスのヘルスチェック"""
        try:
            container = self.manager.docker_client.containers.get(
                sandbox.container_id
            )
            return container.status == "running"
        except Exception:
            return False

    @property
    def stats(self) -> dict:
        """プール統計"""
        return {
            "pool_size": len(self._pool),
            "active_count": len(self._active),
            "target_size": self.config.target_size,
        }
```

### 4.4.2 パフォーマンス目標

| メトリクス | 現行 | Phase 1 | Phase 3 (Warm Pool) |
|-----------|------|---------|---------------------|
| SDK起動レイテンシ | ~100ms | ~600ms (+500ms コンテナ起動) | ~50ms (プールから取得) |
| メモリオーバーヘッド/セッション | 0 (共有) | ~50MB | ~50MB (idle) |
| 最大同時セッション | CPU制約のみ | 設定値 | max_size 制約 |

## 4.5 Phase 4: gVisor 統合 (Optional)

### 目標

Docker ランタイムを gVisor (runsc) に変更し、カーネルレベルの隔離を追加。

### 変更点

```json
// /etc/docker/daemon.json
{
  "runtimes": {
    "runsc": {
      "path": "/usr/local/bin/runsc"
    }
  }
}
```

```python
# manager.py の _create_container を更新
container = self.docker_client.containers.run(
    ...
    runtime="runsc",  # gVisor ランタイムを指定
    ...
)
```

### gVisor の効果

| 攻撃ベクトル | Docker 標準 | Docker + gVisor |
|-------------|------------|----------------|
| カーネル脆弱性 | 脆弱 | 保護 (ユーザー空間カーネル) |
| コンテナエスケープ | 可能性あり | 極めて困難 |
| syscall 攻撃 | seccomp で緩和 | Sentry で遮断 |

## 4.6 マイグレーション戦略

### Feature Flag によるグラジュアルロールアウト

```python
# app/config.py に追加
class Settings(BaseSettings):
    # サンドボックス設定
    sandbox_enabled: bool = False         # 全体スイッチ
    sandbox_rollout_percent: int = 0      # ロールアウト率 (0-100)
    sandbox_force_tenants: str = ""       # 強制有効テナント (カンマ区切り)
    sandbox_image: str = "ai-agent-sandbox:latest"
    sandbox_network_mode: str = "none"
    sandbox_mem_limit: str = "2g"
    sandbox_cpu_cores: int = 1
    sandbox_pids_limit: int = 256
    sandbox_storage_limit: str = "5g"
    sandbox_warm_pool_target: int = 5
    sandbox_warm_pool_min: int = 2
    sandbox_warm_pool_max: int = 10
```

### ロールアウト計画

```
Week 1-2: sandbox_enabled=true, sandbox_rollout_percent=0
  → 開発環境でテスト
  → sandbox_force_tenants で特定テナントのみ有効化

Week 3:   sandbox_rollout_percent=10
  → 10% のリクエストをサンドボックス実行

Week 4:   sandbox_rollout_percent=50
  → 問題なければ 50% に拡大

Week 5:   sandbox_rollout_percent=100
  → 全リクエストをサンドボックス実行
```

### フォールバック

```python
async def _execute_with_sdk(self, context, options, ...):
    if self._should_use_sandbox(context):
        try:
            async for event in self._execute_in_sandbox(context, options, ...):
                yield event
        except SandboxUnavailableError:
            # サンドボックスが利用不可の場合はフォールバック
            logger.warning("サンドボックス利用不可: 直接実行にフォールバック")
            async for event in self._execute_direct(context, options, ...):
                yield event
    else:
        async for event in self._execute_direct(context, options, ...):
            yield event
```
