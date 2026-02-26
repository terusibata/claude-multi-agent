FROM --platform=linux/arm64 python:3.13-slim AS workspace-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential libffi-dev && rm -rf /var/lib/apt/lists/*

# Node.js 24 LTS (SDK CLIのサブプロセスで使用)
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PIP_REQUIRE_VIRTUALENV=true

COPY workspace-base/workspace-requirements.txt /tmp/workspace-requirements.txt
RUN pip install --no-cache-dir -r /tmp/workspace-requirements.txt

# CLIバイナリに実行権限を付与（pip wheelが実行ビットを落とすことがある）
RUN find /opt/venv/lib -path "*/claude_agent_sdk/_bundled/claude" -exec chmod +x {} \;

COPY workspace_agent/ /opt/workspace_agent/

# PYTHONPATH: /opt配下のworkspace_agentモジュールをインポート可能にする
ENV PYTHONPATH="/opt:$PYTHONPATH"

RUN useradd -m -u 1000 appuser && mkdir -p /workspace && chown appuser:appuser /workspace
USER appuser
WORKDIR /workspace

# ランタイムディレクトリ作成
RUN mkdir -p /home/appuser/.claude /tmp/claude-1000

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -sf http://localhost:8080/ping || exit 1

CMD ["python", "-m", "workspace_agent.main"]
