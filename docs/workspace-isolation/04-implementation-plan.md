# 4. 実装計画

## 4.0 開発方針

> **本プロジェクトは現在開発フェーズにあるため、ベストプラクティスに従い
> 全フェーズの機能を一括で実装する。**
>
> 元のフェーズ分割は本番環境への段階的ロールアウトの指針として残すが、
> コードベースへの変更は一度に行い、Feature Flag で制御する。
> これにより、設計の整合性を保ちながら手戻りを最小化できる。

### 一括実装の理由

1. **設計の整合性**: ライフサイクル管理（セッション固定モデル）はセキュリティ設定と
   密結合しており、分離して実装すると後から大幅な修正が必要になる
2. **テストの効率**: 隔離・セキュリティ・Warm Pool を統合テストできる
3. **Feature Flag による安全性**: `SANDBOX_ENABLED=false` で従来動作、
   `true` で新動作に切り替えられるため、一括実装でもリスクは低い

### 実装順序（コード変更の依存関係に基づく）

```
1. Dockerfile.sandbox + sandbox イメージビルド
2. SandboxManager (コンテナ作成・破棄・ヘルスチェック)
3. セキュリティプロファイル (seccomp / AppArmor / ネットワーク)
4. Warm Pool + セッション固定ライフサイクル
5. 定期クリーンアップ (Scheduled Cleanup)
6. SDK アダプター (Docker exec 統合)
7. ExecuteService 統合 + Feature Flag
8. 監視メトリクス + アラート
9. 統合テスト + 負荷テスト
```

---

## 4.1 フェーズ概要（本番ロールアウト用）

> 以下のフェーズ分割は、本番環境へのロールアウト計画である。
> コード実装自体は上記の方針に従い一括で行う。

```
Phase 1: コンテナ隔離 + セキュリティ + ライフサイクル (一括実装)
  │  サンドボックスコンテナ + セッション固定 + seccomp/AppArmor
  │  + Warm Pool + 定期クリーンアップ + Egress Proxy
  │
Phase 2: 本番ロールアウト (段階的有効化)
  │  Feature Flag で 0% → 10% → 50% → 100%
  │
Phase 3: gVisor 統合 (Optional)
     カーネル隔離の強化
```

## 4.2 コンテナ隔離の基盤構築

### 目標

- エージェント実行を専用コンテナで行う基本フローの確立
- セッション固定コンテナモデルによる会話内状態の維持
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

## 4.4 セッション固定ライフサイクル + 定期クリーンアップ

### 目標

- セッション固定コンテナモデルによる会話内状態の維持
- 定期クリーンアップによるリソース回収
- Warm Pool による新規会話のレイテンシ削減

### 4.4.1 SandboxLifecycleManager 実装

**新規ファイル**: `app/services/sandbox/lifecycle.py`

```python
"""
サンドボックスライフサイクル管理

セッション固定コンテナモデル:
  - コンテナは会話に紐付いて維持される
  - 同一会話の複数メッセージで同じコンテナを再利用
  - pip install 等のセッション内状態が保持される
  - 定期クリーンアップで TTL 超過コンテナを回収
"""
import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from app.services.sandbox.manager import Sandbox, SandboxManager, SandboxStatus

logger = structlog.get_logger(__name__)


@dataclass
class LifecycleConfig:
    # Warm Pool
    warm_pool_min: int = 2
    warm_pool_max: int = 10
    warm_pool_target: int = 5
    replenish_interval_seconds: int = 10

    # セッション固定
    session_idle_timeout: int = 86400    # 24時間 (最終活動からの TTL)
    session_max_lifetime: int = 172800   # 48時間 (コンテナ絶対寿命)

    # 定期クリーンアップ
    cleanup_interval_seconds: int = 3600  # 1時間ごとにスイープ
    cleanup_stagger_max: int = 1800       # 30分間で分散破棄
    cleanup_grace_period: int = 300       # IDLE→破棄の猶予 (5分)

    # ヘルスチェック
    health_check_interval: int = 60


class SandboxLifecycleManager:
    """
    セッション固定コンテナのライフサイクルを管理。

    Warm Pool (未割り当てコンテナ) と
    Session Registry (会話に紐付いたコンテナ) を統合管理する。
    """

    def __init__(
        self,
        manager: SandboxManager,
        config: LifecycleConfig | None = None,
    ):
        self.manager = manager
        self.config = config or LifecycleConfig()

        # Warm Pool: 未割り当てコンテナ
        self._warm_pool: deque[Sandbox] = deque()

        # Session Registry: conversation_id -> Sandbox
        self._sessions: dict[str, Sandbox] = {}

        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """ライフサイクル管理を起動"""
        self._running = True

        # Backend 再起動時: 既存コンテナを検出して復旧
        await self._recover_existing_containers()

        # バックグラウンドタスク起動
        self._tasks = [
            asyncio.create_task(self._replenish_loop()),
            asyncio.create_task(self._health_check_loop()),
            asyncio.create_task(self._cleanup_loop()),
        ]

        await self._replenish_pool()
        logger.info(
            "ライフサイクル管理起動",
            warm_pool=len(self._warm_pool),
            sessions=len(self._sessions),
        )

    async def acquire_or_reuse(
        self,
        conversation_id: str,
        workspace_path: str,
        env: dict[str, str],
        **kwargs,
    ) -> Sandbox:
        """
        会話に紐付くコンテナを取得 (セッション固定)

        1. 既存コンテナがあればヘルスチェック後に再利用
        2. なければ Warm Pool から取得
        3. Pool も空ならオンデマンド作成
        """
        # 1. 既存コンテナの再利用
        existing = self._sessions.get(conversation_id)
        if existing and await existing.is_healthy():
            existing.status = SandboxStatus.RUNNING
            existing.last_activity_at = datetime.now(timezone.utc)
            logger.info(
                "既存コンテナ再利用",
                conversation_id=conversation_id,
                container_id=existing.container_id[:12],
            )
            return existing

        # 既存が不健全な場合は破棄
        if existing:
            logger.warning(
                "不健全なコンテナを破棄して再作成",
                container_id=existing.container_id[:12],
            )
            await self.manager.release(existing)
            del self._sessions[conversation_id]

        # 2. Warm Pool から取得
        sandbox = None
        if self._warm_pool:
            sandbox = self._warm_pool.popleft()
            # 会話に紐付
            sandbox.conversation_id = conversation_id
            sandbox.workspace_path = workspace_path
            # ワークスペースを bind mount (動的)
            await self.manager.bind_workspace(sandbox, workspace_path)
        else:
            # 3. オンデマンド作成
            logger.warning("Warm Pool 空: オンデマンド作成")
            sandbox = await self.manager.acquire(
                conversation_id=conversation_id,
                workspace_path=workspace_path,
                env=env,
                **kwargs,
            )

        sandbox.status = SandboxStatus.RUNNING
        sandbox.last_activity_at = datetime.now(timezone.utc)
        self._sessions[conversation_id] = sandbox
        return sandbox

    async def mark_idle(self, conversation_id: str) -> None:
        """SDK 完了後、コンテナを IDLE に遷移（破棄しない）"""
        sandbox = self._sessions.get(conversation_id)
        if sandbox:
            sandbox.status = SandboxStatus.IDLE
            sandbox.last_activity_at = datetime.now(timezone.utc)

    # --- 定期クリーンアップ ---

    async def _cleanup_loop(self) -> None:
        """定期的に TTL 超過コンテナをクリーンアップ"""
        while self._running:
            await asyncio.sleep(self.config.cleanup_interval_seconds)
            await self._cleanup_expired()

    async def _cleanup_expired(self) -> dict:
        """TTL 超過コンテナを破棄"""
        now = datetime.now(timezone.utc)
        expired: list[str] = []

        for conv_id, sandbox in self._sessions.items():
            # RUNNING 状態は絶対にスキップ
            if sandbox.status == SandboxStatus.RUNNING:
                continue

            idle_seconds = (now - sandbox.last_activity_at).total_seconds()
            lifetime_seconds = (now - sandbox.created_at).total_seconds()

            if (idle_seconds > self.config.session_idle_timeout
                    or lifetime_seconds > self.config.session_max_lifetime):
                expired.append(conv_id)

        # 分散破棄（一度に大量破棄しない）
        cleaned = 0
        for conv_id in expired:
            sandbox = self._sessions.pop(conv_id, None)
            if sandbox:
                # ランダム遅延で分散
                delay = random.uniform(0, self.config.cleanup_stagger_max)
                await asyncio.sleep(min(delay, 5))  # 最大5秒待機

                await sandbox.final_sync_and_destroy()
                cleaned += 1
                logger.info(
                    "TTL超過コンテナ破棄",
                    conversation_id=conv_id,
                    container_id=sandbox.container_id[:12],
                )

        report = {
            "checked": len(self._sessions) + len(expired),
            "expired": len(expired),
            "cleaned": cleaned,
        }
        if cleaned > 0:
            logger.info("クリーンアップ完了", **report)
        return report

    # --- 以下 Warm Pool / ヘルスチェック / リカバリ ---
    # (省略: 02-isolation-strategy.md の設計に準拠)
```

### 4.4.2 パフォーマンス目標

| メトリクス | 現行 | 新設計 (初回) | 新設計 (2回目以降) |
|-----------|------|-------------|-------------------|
| SDK起動レイテンシ | ~100ms | ~600ms (Pool空) / ~50ms (Pool) | **~10ms (既存コンテナ再利用)** |
| メモリオーバーヘッド/セッション | 0 (共有) | ~50MB | ~50MB (idle 維持) |
| pip install 再実行 | N/A | 毎回 | **不要 (コンテナ維持)** |
| 最大同時コンテナ | N/A | warm_pool_max + active sessions | 同左 |

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

### Feature Flag による制御

```python
# app/config.py に追加
class Settings(BaseSettings):
    # === サンドボックス全般 ===
    sandbox_enabled: bool = False         # 全体スイッチ
    sandbox_rollout_percent: int = 0      # ロールアウト率 (0-100)
    sandbox_force_tenants: str = ""       # 強制有効テナント (カンマ区切り)
    sandbox_image: str = "ai-agent-sandbox:latest"

    # === リソース制限 ===
    sandbox_network_mode: str = "none"
    sandbox_mem_limit: str = "2g"
    sandbox_cpu_cores: int = 1
    sandbox_pids_limit: int = 256
    sandbox_storage_limit: str = "5g"

    # === Warm Pool ===
    sandbox_warm_pool_target: int = 5
    sandbox_warm_pool_min: int = 2
    sandbox_warm_pool_max: int = 10

    # === セッション固定ライフサイクル ===
    sandbox_session_idle_timeout: int = 86400    # 24時間
    sandbox_session_max_lifetime: int = 172800   # 48時間
    sandbox_cleanup_interval: int = 3600         # 1時間ごとにスイープ
```

### ロールアウト計画

> コード実装は一括で行い、Feature Flag で本番環境に段階的に展開する。

```
開発環境: sandbox_enabled=true (全テナント有効)
  → 全機能をテスト

ステージング: sandbox_enabled=true, sandbox_rollout_percent=100
  → 本番同等環境で負荷テスト

本番 Week 1: sandbox_rollout_percent=0
  → sandbox_force_tenants で社内テナントのみ有効化
  → 実運用での挙動確認

本番 Week 2: sandbox_rollout_percent=10
  → 10% のリクエストをサンドボックス実行

本番 Week 3: sandbox_rollout_percent=50
  → 問題なければ 50% に拡大

本番 Week 4: sandbox_rollout_percent=100
  → 全リクエストをサンドボックス実行
  → フォールバックコードは残存させ、緊急時に切り戻し可能に
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

## 4.7 変更ファイル総覧（一括実装）

> Phase 1-3 を一括で実装する場合の全変更ファイル一覧。

| ファイル | 操作 | 説明 |
|---------|------|------|
| `Dockerfile.sandbox` | 新規 | サンドボックスイメージ |
| `requirements-sandbox.txt` | 新規 | 事前インストールパッケージ |
| `sandbox-pip.conf` | 新規 | pip 設定 |
| `sandbox/seccomp/sandbox-seccomp.json` | 新規 | seccomp プロファイル |
| `sandbox/apparmor/sandbox-profile` | 新規 | AppArmor プロファイル |
| `sandbox/egress-proxy/squid.conf` | 新規 | Egress Proxy 設定 |
| `sandbox/egress-proxy/allowlist.txt` | 新規 | 許可ドメインリスト |
| `app/services/sandbox/__init__.py` | 新規 | パッケージ初期化 |
| `app/services/sandbox/manager.py` | 新規 | SandboxManager (コンテナ操作) |
| `app/services/sandbox/lifecycle.py` | 新規 | SandboxLifecycleManager (セッション固定+クリーンアップ) |
| `app/services/sandbox/sdk_adapter.py` | 新規 | SDK アダプター (Docker exec 統合) |
| `app/services/sandbox/config.py` | 新規 | サンドボックス設定 |
| `app/services/sandbox/network.py` | 新規 | ネットワークモード管理 |
| `app/services/sandbox/credentials.py` | 新規 | 一時認証情報管理 |
| `app/services/sandbox/metrics.py` | 新規 | 監視メトリクス |
| `app/services/execute_service.py` | 変更 | SDK 実行部分 → サンドボックス統合 |
| `app/services/execute/options_builder.py` | 変更 | cwd の決定ロジック変更 |
| `app/services/workspace_service.py` | 変更 | S3 同期フロー変更（セッション固定対応） |
| `app/config.py` | 変更 | サンドボックス設定項目追加 |
| `docker-compose.yml` | 変更 | Docker socket マウント + Egress Proxy 追加 |
| `requirements.txt` | 変更 | `docker` パッケージ追加 |
