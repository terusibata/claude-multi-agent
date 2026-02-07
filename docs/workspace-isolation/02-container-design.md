# 02 - コンテナ隔離設計

## コンテナアーキテクチャ

### 隔離モデルの選択

| 技術 | 隔離レベル | コールドスタート | 複雑度 | 推奨度 |
|------|-----------|-----------------|--------|--------|
| 標準 Docker コンテナ | 中 | ~10ms | 低 | **Phase 1 推奨** |
| gVisor (runsc) | 中〜高 | 50-100ms | 中 | Phase 2 検討 |
| Firecracker microVM | 高 | 100-200ms | 高 | 将来検討 |

**Phase 1: 標準 Docker + セキュリティ強化** を推奨。
理由: 既存の Docker 基盤を活用でき、セキュリティオプション（seccomp、AppArmor、capability drop）で十分な隔離を実現できる。

### コンテナ構成

```
┌─ Workspace Container ──────────────────────────┐
│                                                 │
│  User: appuser (non-root, UID 1000)            │
│                                                 │
│  ┌─ Python venv (/opt/venv) ─────────────────┐ │
│  │  プリインストール済みライブラリ             │ │
│  │  + ユーザーが pip install したライブラリ    │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  ┌─ Claude Agent SDK ────────────────────────┐ │
│  │  ClaudeSDKClient                          │ │
│  │  ├─ Claude Code CLI (Node.js subprocess)  │ │
│  │  └─ MCP Servers (in-process)              │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  ┌─ Workspace Directory (/workspace) ────────┐ │
│  │  ユーザーファイル + AI生成ファイル          │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  Resource Limits:                               │
│    CPU: 2 cores, Memory: 4GB, Disk: 5GB        │
│    PIDs: 256, Network: restricted               │
└─────────────────────────────────────────────────┘
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

COPY workspace-requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/workspace-requirements.txt

# Claude Agent SDK
RUN pip install --no-cache-dir claude-agent-sdk==0.1.23

# 非root ユーザー
RUN useradd -m -u 1000 appuser
RUN mkdir -p /workspace && chown appuser:appuser /workspace

USER appuser
WORKDIR /workspace
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
```

## 通信設計

### Backend ↔ Workspace Container 間通信

```
Backend (FastAPI)
    │
    │  HTTP/gRPC (内部ネットワーク)
    │
    ▼
Workspace Container
    │
    ├─ Agent Executor (Python process)
    │   ├─ Claude SDK Client
    │   ├─ メッセージストリーミング (WebSocket or SSE)
    │   └─ ファイル同期 (S3 upload/download)
    │
    └─ Health Check Endpoint (:8080/health)
```

### 通信プロトコル選択

| 方式 | メリット | デメリット | 推奨 |
|------|---------|-----------|------|
| Docker exec + stdin/stdout | シンプル | スケーラビリティ低 | × |
| HTTP API (各コンテナ内) | 標準的、ロードバランス容易 | オーバーヘッド | **○** |
| gRPC | 高速、型安全 | 複雑度が高い | △ |

**推奨: 各コンテナ内に軽量 HTTP サーバーを配置。**

Backend → Container への通信は内部ネットワーク経由の HTTP。
Container → Backend への通信は WebSocket で SSE イベントを中継。

### コンテナ内プロセス構成

```python
# workspace_agent.py (コンテナ内で実行)
from fastapi import FastAPI
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

app = FastAPI()

@app.post("/execute")
async def execute(request: ExecuteRequest):
    """Agent SDK を実行し、結果をストリーミング返却"""
    options = build_options(request)
    async with ClaudeSDKClient(options=options) as client:
        await client.query(request.user_input)
        async for message in client.receive_response():
            yield format_sse_event(message)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

## Container Orchestrator 設計

### Backend 側のコンポーネント

```python
class ContainerOrchestrator:
    """ワークスペースコンテナのライフサイクル管理"""

    async def get_or_create(self, conversation_id: str) -> ContainerInfo:
        """既存コンテナの取得、または新規作成"""

    async def destroy(self, conversation_id: str) -> None:
        """コンテナの破棄とクリーンアップ"""

    async def execute(self, conversation_id: str, request: ExecuteRequest) -> AsyncIterator:
        """コンテナ内でエージェントを実行"""

    async def sync_files_to_container(self, conversation_id: str) -> None:
        """S3 → コンテナへファイル同期"""

    async def sync_files_from_container(self, conversation_id: str) -> None:
        """コンテナ → S3 へファイル同期（AI生成ファイル）"""
```

### コンテナ作成フロー

```
1. ユーザーリクエスト受信
2. conversation_id でコンテナ検索
   ├─ 存在する → コンテナに直接リクエスト転送
   └─ 存在しない → 3へ
3. Warm Pool からコンテナ取得
   ├─ プール内にある → 取得して初期化
   └─ プール空 → 新規コンテナ作成
4. S3 からワークスペースファイルを同期
5. コンテナ内でエージェント実行
6. 結果を SSE ストリーミングでフロントエンドへ中継
7. AI 生成ファイルを S3 に同期
8. コンテナ状態を Redis に記録（TTL 管理用）
```
