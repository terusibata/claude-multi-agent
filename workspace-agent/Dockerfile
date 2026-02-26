# ワークスペースエージェント Dockerfile (TypeScript)
# Node.js 24 ベースで Claude Agent SDK (TS) を実行

# ==========================================
# ビルドステージ
# ==========================================
FROM --platform=linux/arm64 node:24-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY workspace-agent/package*.json ./
RUN npm ci

COPY workspace-agent/tsconfig.json ./
COPY workspace-agent/src/ ./src/
RUN npm run build

# ==========================================
# 本番ステージ
# ==========================================
FROM --platform=linux/arm64 node:24-slim AS workspace-agent

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git && rm -rf /var/lib/apt/lists/*

# エージェントアプリケーション配置
WORKDIR /opt/workspace-agent
COPY --from=builder /build/package*.json ./
RUN npm ci --production
COPY --from=builder /build/dist/ ./dist/

# CLI バイナリに実行権限を付与
RUN find node_modules -path "*/_bundled/claude" -exec chmod +x {} \; 2>/dev/null || true

# ユーザー・ディレクトリ設定
RUN useradd -m -u 1000 appuser && mkdir -p /workspace && chown appuser:appuser /workspace
USER appuser
WORKDIR /workspace

# ランタイムディレクトリ作成
RUN mkdir -p /home/appuser/.claude /tmp/claude-1000

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -sf http://localhost:8080/ping || exit 1

CMD ["node", "/opt/workspace-agent/dist/index.js"]
