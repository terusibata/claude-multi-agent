# AIエージェントバックエンド Dockerfile
# マルチステージビルドで本番イメージを最適化

# ==========================================
# ビルドステージ
# ==========================================
FROM python:3.11-slim AS builder

# ビルド用依存パッケージのインストール
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 依存パッケージをビルド
WORKDIR /build
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ==========================================
# 本番ステージ
# ==========================================
FROM python:3.11-slim AS production

# 実行時に必要な最小限のパッケージのみインストール
RUN apt-get update && apt-get install -y \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Node.js のインストール（Claude Agent SDKに必要）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 非rootユーザーの作成
RUN useradd -m -u 1000 appuser

# Docker Socket アクセス用グループ設定
# ホスト側のdocker GIDに合わせてランタイム時にgroup_addで追加
ARG DOCKER_GID=999
RUN groupadd -g ${DOCKER_GID} docker 2>/dev/null || true \
    && usermod -aG docker appuser 2>/dev/null || true

# 作業ディレクトリの設定
WORKDIR /app

# ビルドステージからPythonパッケージをコピー
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# アプリケーションコードのコピー
COPY --chown=appuser:appuser app/ /app/app/
COPY --chown=appuser:appuser alembic/ /app/alembic/
COPY --chown=appuser:appuser alembic.ini /app/
COPY --chown=appuser:appuser entrypoint.sh /app/
COPY --chown=appuser:appuser deployment/seccomp/ /app/deployment/seccomp/

# エントリーポイントスクリプトに実行権限を付与
RUN chmod +x /app/entrypoint.sh

# Skills ディレクトリの作成
RUN mkdir -p /skills && chown appuser:appuser /skills

# ワークスペース用ディレクトリの作成
RUN mkdir -p /var/lib/aiagent/workspaces && chown -R appuser:appuser /var/lib/aiagent

# ワークスペースSocket用ディレクトリの作成
RUN mkdir -p /var/run/workspace-sockets && chown appuser:appuser /var/run/workspace-sockets

# NOTE: USER appuserは設定しない。entrypoint.shでrootとしてソケットディレクトリの
# 権限を修正した後、gosuでappuserに切り替えてアプリケーションを起動する。

# 環境変数の設定
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PATH=/home/appuser/.local/bin:$PATH

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# ポート公開
EXPOSE 8000

# エントリーポイント（マイグレーション自動実行）
ENTRYPOINT ["/app/entrypoint.sh"]

# アプリケーション起動（本番用設定）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--timeout-keep-alive", "65"]

# ==========================================
# 開発ステージ
# ==========================================
FROM production AS development

# 開発用依存パッケージをインストール
COPY requirements.txt requirements-dev.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt

# 開発用のコマンド（auto-reload有効）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
