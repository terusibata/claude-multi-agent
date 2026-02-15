FROM python:3.11-slim AS workspace-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential libffi-dev socat && rm -rf /var/lib/apt/lists/*

# Node.js 20 (SDK CLIのサブプロセスで使用)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
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

# エントリポイントスクリプト
COPY workspace-base/entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh

# /var/run/ws を事前作成（手動テスト用の安全策。本番ではBind mountで上書きされる）
RUN mkdir -p /var/run/ws

RUN useradd -m -u 1000 appuser && mkdir -p /workspace && chown appuser:appuser /workspace \
    && chown appuser:appuser /var/run/ws
USER appuser
WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -sf --unix-socket /var/run/ws/agent.sock http://localhost/health || exit 1

ENTRYPOINT ["/opt/entrypoint.sh"]
