# AIエージェントバックエンド Dockerfile
FROM python:3.11-slim

# システム依存パッケージのインストール
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Node.js のインストール（Claude Agent SDKに必要）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 作業ディレクトリの設定
WORKDIR /app

# 依存パッケージのインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードのコピー
COPY . .

# Skills ディレクトリの作成
RUN mkdir -p /skills

# ワークスペース用ディレクトリの作成
RUN mkdir -p /var/lib/aiagent/workspaces

# 非rootユーザーの作成
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app /skills /var/lib/aiagent
USER appuser

# 環境変数の設定
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ポート公開
EXPOSE 8000

# アプリケーション起動
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
