# 02 - コンテナ隔離設計

## コンテナアーキテクチャ

### 隔離モデルの選択

| 技術 | 隔離レベル | コールドスタート | 複雑度 | 推奨度 |
|------|-----------|-----------------|--------|--------|
| 標準 Docker コンテナ | 中 | ~10ms | 低 | **Phase 1 推奨** |
| gVisor (runsc) | 中〜高 | 50-100ms | 中 | Phase 2 検討 |
| Firecracker microVM | 高 | 100-200ms | 高 | 将来検討 |

**Phase 1: 標準 Docker + セキュリティ強化** を推奨。
理由: 本システムの脅威モデルは「AI Agent の事故防止」であり、敵対的攻撃者のコード実行ではない。
Docker + seccomp + cgroups + ネットワーク隔離で十分な防御を実現できる。

### コンテナ構成

```
┌─ Workspace Container ──────────────────────────────────┐
│                                                         │
│  User: appuser (non-root, UID 1000)                    │
│                                                         │
│  ┌─ Python venv (/opt/venv) ── Docker Volume ────────┐ │
│  │  プリインストール済みライブラリ (イメージ由来)       │ │
│  │  + ユーザーが pip install したライブラリ (揮発)     │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─ Agent Executor (Python process) ─────────────────┐ │
│  │  FastAPI (lightweight HTTP server)                 │ │
│  │  ├─ ClaudeSDKClient (in-process)                  │ │
│  │  ├─ Claude Code CLI (Node.js subprocess, SDK管理)  │ │
│  │  └─ Builtin MCP Servers (file-tools等, in-process)│ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─ Workspace Directory (/workspace) ────────────────┐ │
│  │  ユーザーファイル + AI生成ファイル                   │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Resource Limits:                                       │
│    CPU: 2 cores, Memory: 4GB, PIDs: 256                │
│    Disk: 5GB (overlay2 + XFS pquota)                   │
│    Network: proxy 経由のみ                              │
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
  CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "workspace_agent.main:app", "--host", "0.0.0.0", "--port", "8080"]
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

# コンテナ内 HTTP サーバー
fastapi
uvicorn[standard]
```

## 通信設計

### 全体構成

```
Frontend
    │ SSE
    ▼
Backend (FastAPI)
    │
    ├─ ContainerOrchestrator (Docker API via aiodocker)
    │   ├─ コンテナ作成・破棄
    │   └─ Warm Pool 管理
    │
    │ HTTP (workspace-network 内部)
    ▼
Workspace Container (:8080)
    │
    ├─ /execute (POST) ← Agent 実行リクエスト
    ├─ /health  (GET)  ← ヘルスチェック
    └─ /files   (GET)  ← ファイル同期用
```

### 認証情報の注入（Anthropic 公式推奨パターン）

```
┌─ Host ─────────────────────────────────────────────────┐
│                                                         │
│  ┌─ Credential Proxy ────────────────────────────────┐ │
│  │  - AWS 認証情報を保持                              │ │
│  │  - Bedrock API リクエストに認証ヘッダを注入         │ │
│  │  - ドメインホワイトリスト（PyPI, Bedrock）          │ │
│  │  - 全リクエストをログ                              │ │
│  └───────────┬───────────────────────────────────────┘ │
│              │ Unix Socket (/run/proxy.sock)            │
│              │                                         │
│  ┌─ Workspace Container ─────────────────────────────┐ │
│  │  --network none (ネットワークスタックなし)          │ │
│  │  Unix Socket のみで外部通信                        │ │
│  │  ※ コンテナ内から直接 API コールできない            │ │
│  │  ※ 認証情報がコンテナ内に存在しない                │ │
│  └───────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

**ただし、Phase 1 では簡易版を推奨:**
- `--network none` ではなく、workspace-network (制限付き) を使用
- 認証情報は Docker Secrets (`/run/secrets/`) 経由で注入
- Phase 2 で Unix Socket プロキシに移行

### Phase 1: 簡易版の認証情報注入

```python
# Docker Secrets として注入
CONTAINER_SECRETS = {
    "aws_access_key_id": "/run/secrets/aws_access_key_id",
    "aws_secret_access_key": "/run/secrets/aws_secret_access_key",
}
# コンテナ内で /run/secrets/ からファイル読み取り
# 環境変数としては公開しない
```

### コンテナ内プロセス構成

```python
# workspace_agent/main.py (コンテナ内で実行)
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.post("/execute")
async def execute(request: ExecuteRequest) -> StreamingResponse:
    """Agent SDK を実行し、結果を SSE ストリーミング返却"""

    # 認証情報を Docker Secrets から読み取り
    env = load_credentials_from_secrets()

    options = build_sdk_options(
        system_prompt=request.system_prompt,
        model=request.model,
        cwd="/workspace",
        env=env,
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
```

## Container Orchestrator 設計

### Backend 側のコンポーネント

```python
class ContainerOrchestrator:
    """ワークスペースコンテナのライフサイクル管理"""

    def __init__(self, docker_client: aiodocker.Docker, redis: Redis):
        self.docker = docker_client
        self.redis = redis
        self.warm_pool = WarmPoolManager(docker_client, redis)

    async def get_or_create(self, conversation_id: str) -> ContainerInfo:
        """既存コンテナの取得、または Warm Pool から割り当て"""

    async def destroy(self, conversation_id: str) -> None:
        """コンテナの破棄とクリーンアップ"""

    async def execute(self, conversation_id: str, request: ExecuteRequest) -> AsyncIterator:
        """コンテナ内でエージェントを実行（HTTP 経由）"""

    async def sync_files_to_container(self, conversation_id: str) -> None:
        """S3 → コンテナへファイル同期（Docker cp or HTTP API）"""

    async def sync_files_from_container(self, conversation_id: str) -> None:
        """コンテナ → S3 へファイル同期"""
```

### コンテナ作成フロー

```
1. ユーザーリクエスト受信
2. Redis で conversation_id → container 情報を検索
   ├─ 存在する → TTL リセット → コンテナに HTTP リクエスト転送
   └─ 存在しない → 3へ
3. Warm Pool からコンテナ取得
   ├─ プール内にある → 取得
   └─ プール空 → 新規コンテナ作成（数秒かかる）
4. コンテナに conversation_id をラベル付け
5. S3 からワークスペースファイルを同期 (docker cp)
6. Redis に container 情報を記録 (TTL: 3600s)
7. コンテナの /execute に HTTP POST
8. SSE レスポンスを Backend → Frontend に中継
9. 完了後、AI 生成ファイルを S3 に同期
```
