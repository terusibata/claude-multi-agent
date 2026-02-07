FROM python:3.11-slim AS workspace-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential libffi-dev && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PIP_REQUIRE_VIRTUALENV=true

COPY workspace-requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/workspace-requirements.txt

COPY workspace_agent/ /opt/workspace_agent/

RUN useradd -m -u 1000 appuser && mkdir -p /workspace && chown appuser:appuser /workspace
USER appuser
WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http+unix://%2Fvar%2Frun%2Fagent.sock/health')" || exit 1

CMD ["python", "-m", "workspace_agent.main"]
