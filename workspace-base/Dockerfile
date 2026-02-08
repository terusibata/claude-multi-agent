FROM python:3.11-slim AS workspace-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential libffi-dev socat && rm -rf /var/lib/apt/lists/*

# Node.js 20 (SDK CLIのサブプロセスで使用)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# global-agent: Node.js 20でHTTP_PROXYをfetch()に適用するために必要
RUN npm install -g global-agent

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PIP_REQUIRE_VIRTUALENV=true

COPY workspace-base/workspace-requirements.txt /tmp/workspace-requirements.txt
RUN pip install --no-cache-dir -r /tmp/workspace-requirements.txt

COPY workspace_agent/ /opt/workspace_agent/

# PYTHONPATH: /opt配下のworkspace_agentモジュールをインポート可能にする
ENV PYTHONPATH="/opt:$PYTHONPATH"

# エントリポイントスクリプト
COPY workspace-base/entrypoint.sh /opt/entrypoint.sh

RUN useradd -m -u 1000 appuser && mkdir -p /workspace && chown appuser:appuser /workspace
USER appuser
WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -sf --unix-socket /var/run/ws/agent.sock http://localhost/health || exit 1

ENTRYPOINT ["/opt/entrypoint.sh"]
