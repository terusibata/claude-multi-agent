# 02 - コンテナ隔離設計

## コンテナアーキテクチャ

### 隔離モデルの選択

| 技術 | 隔離レベル | コールドスタート | 複雑度 | 推奨度 |
|------|-----------|-----------------|--------|--------|
| sandbox-runtime | 良好 | ほぼ0 | 低 | 軽量用途向け |
| 標準 Docker コンテナ | 中 | ~10ms | 低〜中 | **Phase 1 推奨** |
| gVisor (runsc) | 中〜高 | 50-100ms | 中 | Phase 2 検討 |
| Firecracker microVM | 高 | 100-200ms | 高 | 将来検討 |

**Phase 1: 標準 Docker + `--network none` + Unix Socket Proxy + seccomp** を推奨。

理由:
- 本システムの脅威モデルは「AI Agent の事故防止」であり、敵対的攻撃者のコード実行ではない
- Docker + seccomp + cgroups + `--network none` + Unix Socket Proxy で十分な防御を実現できる
- Anthropic 公式セキュアデプロイメントガイドで推奨されるコンテナ構成に一致
- `@anthropic-ai/sandbox-runtime` と同一のネットワークアーキテクチャを採用

> **sandbox-runtime との比較**: Anthropic 公式の `@anthropic-ai/sandbox-runtime` は
> OS レベルのサンドボックス（Linux: bubblewrap、macOS: sandbox-exec）を使用する軽量な隔離ツール。
> Docker 不要でセットアップが簡単だが、コンテナ単位のリソース制御（CPU/Memory/PIDs）や
> Warm Pool によるコールドスタート最適化が必要な本システムでは Docker ベースを採用する。
> ただし、sandbox-runtime の「`--network none` + Unix Socket Proxy」パターンは本設計に取り入れる。

### コンテナ構成

```
┌─ Workspace Container ──────────────────────────────────┐
│                                                         │
│  --network none (ネットワークスタック完全排除)           │
│  User: appuser (non-root, UID 1000)                    │
│                                                         │
│  ┌─ Python venv (/opt/venv) ── Docker Volume ────────┐ │
│  │  プリインストール済みライブラリ (イメージ由来)       │ │
│  │  + ユーザーが pip install したライブラリ (揮発)     │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─ Agent Executor (Python process) ─────────────────┐ │
│  │  FastAPI (lightweight HTTP server over Unix Socket)│ │
│  │  ├─ ClaudeSDKClient (in-process)                  │ │
│  │  ├─ Claude Code CLI (Node.js subprocess, SDK管理)  │ │
│  │  └─ Builtin MCP Servers (file-tools等, in-process)│ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─ Workspace Directory (/workspace) ────────────────┐ │
│  │  ユーザーファイル + AI生成ファイル                   │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  通信: Unix Socket (/var/run/proxy.sock) のみ          │
│  ※ コンテナ内から直接ネットワーク通信は一切不可         │
│                                                         │
│  Resource Limits:                                       │
│    CPU: 2 cores, Memory: 2GB, PIDs: 100                │
│    Disk: 5GB (overlay2 + XFS pquota)                   │
│    seccomp: Docker デフォルトプロファイル               │
└─────────────────────────────────────────────────────────┘
```

### ベースイメージ設計

```dockerfile
FROM python:3.11-slim AS workspace-base

# システム依存パッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Node.js (Claude Agent SDK の CLI が必要)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (semver管理、定期的にベースイメージを再ビルドして更新)
RUN npm install -g @anthropic-ai/claude-code

# Python venv + プリインストールライブラリ
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PIP_REQUIRE_VIRTUALENV=true

COPY workspace-requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/workspace-requirements.txt

# Claude Agent SDK
RUN pip install --no-cache-dir claude-agent-sdk==0.1.23

# Agent Executor (コンテナ内の HTTP サーバー)
COPY workspace_agent/ /opt/workspace_agent/

# 非root ユーザー
RUN useradd -m -u 1000 appuser
RUN mkdir -p /workspace && chown appuser:appuser /workspace

USER appuser
WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http+unix://%2Fvar%2Frun%2Fagent.sock/health')" || exit 1

CMD ["python", "-m", "workspace_agent.main"]
```

### workspace-requirements.txt（プリインストール）

```
# データ分析
numpy
pandas
scipy
scikit-learn
statsmodels

# 可視化
matplotlib
seaborn
plotly

# ファイル処理
openpyxl
python-docx
pymupdf
Pillow

# Web / API
requests
httpx
beautifulsoup4
lxml

# ユーティリティ
pyyaml
python-dotenv
tqdm
rich

# コンテナ内サーバー
fastapi
uvicorn[standard]
```

> **ベースイメージ更新戦略**: Claude Code CLI は semver でバージョン管理されている。
> CI/CD パイプラインで週次（または CLI メジャーバージョン更新時）にベースイメージを再ビルドし、
> 最新の CLI とセキュリティパッチを反映する。

## 通信設計

### `--network none` + Unix Socket アーキテクチャ

Anthropic 公式セキュアデプロイメントガイド準拠の構成。
コンテナにネットワークインターフェースを一切持たせず、Unix Socket のみで通信する。

```
┌─ Host ─────────────────────────────────────────────────┐
│                                                         │
│  Frontend                                               │
│    │ SSE                                                │
│    ▼                                                    │
│  Backend (FastAPI)                                      │
│    │                                                    │
│    ├─ ContainerOrchestrator (Docker API via aiodocker)  │
│    │   ├─ コンテナ作成・破棄                             │
│    │   └─ Warm Pool 管理                                │
│    │                                                    │
│    │ Unix Socket (/var/run/ws/{container_id}/agent.sock)│
│    ▼                                                    │
│  ┌─ Credential Injection Proxy ─────────────────────┐  │
│  │  - AWS 認証情報を保持（コンテナには渡さない）      │  │
│  │  - Bedrock API リクエストに認証ヘッダを注入         │  │
│  │  - ドメインホワイトリスト適用                       │  │
│  │  - 全リクエストをログ（監査証跡）                   │  │
│  └───────────┬───────────────────────────────────────┘  │
│              │ Unix Socket (/var/run/ws/{id}/proxy.sock) │
│              │                                          │
│  ┌─ Workspace Container (--network none) ────────────┐  │
│  │  ネットワークインターフェースなし                   │  │
│  │  Unix Socket 経由のみで外部通信                    │  │
│  │  ※ コンテナ内から直接 API コールできない            │  │
│  │  ※ 認証情報がコンテナ内に一切存在しない            │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Credential Injection Proxy

```python
# credential_proxy/proxy.py
"""
Unix Socket ベースの Credential Injection Proxy。
Anthropic 公式推奨パターンに準拠。

コンテナからの全リクエストをインターセプトし:
1. ドメインホワイトリストを適用
2. 認証情報（AWS Bedrock 等）をヘッダーに注入
3. 全リクエストを監査ログに記録
4. 宛先サーバーにリクエストを転送
"""
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DOMAIN_WHITELIST: list[str] = [
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "api.anthropic.com",
    # Bedrock リージョナルエンドポイント
    "bedrock-runtime.us-east-1.amazonaws.com",
    "bedrock-runtime.us-west-2.amazonaws.com",
    "bedrock-runtime.ap-northeast-1.amazonaws.com",
]


@dataclass
class ProxyConfig:
    """Proxy 設定"""
    whitelist: list[str]
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    log_all_requests: bool = True


class CredentialInjectionProxy:
    """
    Unix Socket で listen し、コンテナからのリクエストを処理する。
    認証情報はプロキシ内にのみ保持され、コンテナには渡されない。
    """

    def __init__(self, config: ProxyConfig, socket_path: str) -> None:
        self.config = config
        self.socket_path = socket_path

    async def handle_request(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        """リクエストを検証・認証情報注入・転送"""
        # 1. ドメインホワイトリスト検証
        if not self._is_allowed_domain(url):
            logger.warning("Blocked request to %s", url)
            return 403, {}, b"Domain not in whitelist"

        # 2. Bedrock リクエストの場合、AWS SigV4 署名を注入
        if "bedrock-runtime" in url:
            headers = self._inject_aws_credentials(headers, url, body)

        # 3. 監査ログ
        if self.config.log_all_requests:
            logger.info("Proxy: %s %s", method, url)

        # 4. 宛先に転送
        return await self._forward_request(method, url, headers, body)

    def _is_allowed_domain(self, url: str) -> bool:
        """ドメインホワイトリストに含まれるか検証"""
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname or ""
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in self.config.whitelist
        )

    def _inject_aws_credentials(
        self, headers: dict[str, str], url: str, body: bytes
    ) -> dict[str, str]:
        """AWS SigV4 署名を生成してヘッダーに注入"""
        # botocore の SigV4 署名ロジックを使用
        # 認証情報はこのプロキシプロセス内にのみ存在する
        ...
        return headers

    async def _forward_request(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        """実際のリクエスト転送"""
        ...
```

### コンテナ内の環境変数設定

```python
# コンテナ内では認証情報を持たず、プロキシ経由で通信
CONTAINER_ENV = {
    # Anthropic API はプロキシ経由
    "ANTHROPIC_BASE_URL": "http+unix:///var/run/proxy.sock",
    # pip / npm もプロキシ経由
    "HTTP_PROXY": "http+unix:///var/run/proxy.sock",
    "HTTPS_PROXY": "http+unix:///var/run/proxy.sock",
    # 認証情報は一切渡さない
    # AWS_ACCESS_KEY_ID: 設定しない
    # AWS_SECRET_ACCESS_KEY: 設定しない
}
```

### コンテナ内プロセス構成

```python
# workspace_agent/main.py (コンテナ内で実行)
"""
コンテナ内 Agent Executor。
Unix Socket で Backend からのリクエストを受信し、
Claude Agent SDK を実行してストリーミングレスポンスを返却する。

認証情報はコンテナ内に存在せず、外部 Proxy 経由で Bedrock API に接続する。
"""
import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

# Unix Socket パス
AGENT_SOCKET = "/var/run/agent.sock"
PROXY_SOCKET = "/var/run/proxy.sock"


@app.post("/execute")
async def execute(request: ExecuteRequest) -> StreamingResponse:
    """Agent SDK を実行し、結果を SSE ストリーミング返却"""

    # 認証情報はプロキシ側で注入されるため、コンテナ内では不要
    options = build_sdk_options(
        system_prompt=request.system_prompt,
        model=request.model,
        cwd="/workspace",
        env={
            "ANTHROPIC_BASE_URL": f"http+unix://{PROXY_SOCKET}",
        },
        mcp_servers=request.mcp_servers,
    )

    async def event_generator():
        async with ClaudeSDKClient(options=options) as client:
            await client.query(request.user_input)
            async for message in client.receive_response():
                yield format_sse_event(message)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/files")
async def list_files():
    """ワークスペース内のファイル一覧を返却（S3同期用）"""
    ...


if __name__ == "__main__":
    # Unix Socket で listen（TCP ポート不要）
    uvicorn.run(app, uds=AGENT_SOCKET, log_level="info")
```

## Container Orchestrator 設計

### Backend 側のコンポーネント

```python
from dataclasses import dataclass
from typing import AsyncIterator

import aiodocker
from redis.asyncio import Redis


@dataclass
class ContainerInfo:
    """コンテナメタデータ"""
    id: str
    conversation_id: str
    agent_socket: str     # /var/run/ws/{id}/agent.sock
    proxy_socket: str     # /var/run/ws/{id}/proxy.sock
    created_at: str
    last_active_at: str
    status: str


class ContainerOrchestrator:
    """ワークスペースコンテナのライフサイクル管理"""

    def __init__(self, docker_client: aiodocker.Docker, redis: Redis) -> None:
        self.docker = docker_client
        self.redis = redis
        self.warm_pool = WarmPoolManager(docker_client, redis)

    async def get_or_create(self, conversation_id: str) -> ContainerInfo:
        """既存コンテナの取得、または Warm Pool から割り当て"""
        ...

    async def destroy(self, conversation_id: str) -> None:
        """コンテナの破棄とクリーンアップ"""
        ...

    async def execute(
        self, conversation_id: str, request: ExecuteRequest
    ) -> AsyncIterator:
        """コンテナ内でエージェントを実行（Unix Socket 経由）"""
        ...

    async def sync_files_to_container(self, conversation_id: str) -> None:
        """S3 → コンテナへファイル同期（Docker cp）"""
        ...

    async def sync_files_from_container(self, conversation_id: str) -> None:
        """コンテナ → S3 へファイル同期"""
        ...
```

### コンテナ作成フロー

```
1. ユーザーリクエスト受信
2. Redis で conversation_id → container 情報を検索
   ├─ 存在する → TTL リセット → Unix Socket 経由でリクエスト転送
   └─ 存在しない → 3へ
3. Warm Pool からコンテナ取得
   ├─ プール内にある → 取得
   └─ プール空 → 新規コンテナ作成（数秒かかる）
4. コンテナに conversation_id をラベル付け
5. Unix Socket ペア作成（agent.sock + proxy.sock）
6. Credential Injection Proxy を起動（proxy.sock 側で listen）
7. S3 からワークスペースファイルを同期 (docker cp)
8. Redis に container 情報を記録 (TTL: 3600s)
9. agent.sock 経由で /execute に HTTP POST
10. SSE レスポンスを Backend → Frontend に中継
11. 完了後、AI 生成ファイルを S3 に同期
```

### Docker 起動コマンド相当の設定

```python
"""
Anthropic 公式セキュアデプロイメントガイド準拠のコンテナ起動設定。
"""

CONTAINER_CREATE_CONFIG = {
    "Image": "workspace-base:latest",
    "Env": [
        # 認証情報は一切渡さない（プロキシ経由で注入）
        "ANTHROPIC_BASE_URL=http+unix:///var/run/proxy.sock",
        "HTTP_PROXY=http+unix:///var/run/proxy.sock",
        "HTTPS_PROXY=http+unix:///var/run/proxy.sock",
    ],
    "User": "1000:1000",
    "HostConfig": {
        # --- ネットワーク隔離 ---
        # コンテナにネットワークインターフェースを一切持たせない
        # 全通信は Unix Socket 経由でホスト上の Proxy を経由する
        "NetworkMode": "none",

        # --- リソース制限 (Anthropic 推奨値準拠) ---
        "CpuPeriod": 100000,
        "CpuQuota": 200000,       # 2 cores
        "Memory": 2 * 1024**3,    # 2GB (公式推奨)
        "MemorySwap": 2 * 1024**3,  # swap 無効
        "PidsLimit": 100,           # フォークボム対策 (公式推奨: 100)

        # --- Capability 制御 ---
        "CapDrop": ["ALL"],
        "CapAdd": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"],

        # --- セキュリティ強化 ---
        "SecurityOpt": [
            "no-new-privileges:true",
            # Docker デフォルト seccomp プロファイル（44 syscall をブロック）
            # Phase 2 でカスタムプロファイルに移行
        ],
        "Privileged": False,

        # --- ファイルシステム ---
        "ReadonlyRootfs": True,
        "Tmpfs": {
            # /tmp: noexec（プリインストールでビルド不要なため安全）
            "/tmp": "rw,noexec,nosuid,size=512M",
            "/var/tmp": "rw,noexec,nosuid,size=256M",
            "/run": "rw,noexec,nosuid,size=64M",
            "/home/appuser/.cache": "rw,noexec,nosuid,size=512M",
            "/home/appuser": "rw,noexec,nosuid,size=64M",
        },

        # --- ボリュームマウント ---
        "Binds": [
            # ワークスペース（エフェメラル Docker Volume）
            # workspace-{container_id}:/workspace:rw
            # venv（エフェメラル Docker Volume）
            # venv-{container_id}:/opt/venv:rw
            # Unix Socket（ホスト側 Proxy との通信路）
            "/var/run/ws/{container_id}/proxy.sock:/var/run/proxy.sock:ro",
            "/var/run/ws/{container_id}/agent.sock:/var/run/agent.sock:rw",
        ],

        # --- ユーザー名前空間分離 ---
        # userns-remap はデーモンレベルで設定
        # コンテナ内の root がホスト上の非特権ユーザーにマッピングされる

        # --- ディスククォータ ---
        "StorageOpt": {"size": "5G"},

        # --- IPC 隔離 ---
        "IpcMode": "private",
    },
}
```

> **`/tmp` の `noexec` について**: Anthropic 公式ガイドでは `/tmp:rw,noexec,nosuid` が推奨されている。
> プリインストール済みライブラリ（numpy, pandas 等の C 拡張含む）でほとんどのユースケースをカバーするため、
> `/tmp` に `exec` 権限は不要。ソースビルドが必要な特殊パッケージはベースイメージ側で対応する。