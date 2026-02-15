#!/bin/sh
# workspace-base エントリポイント
# 1. CLI が必要とするディレクトリを作成
# 2. socat TCP→UDS リバースプロキシを起動 (background)
# 3. workspace_agent を起動 (foreground)

set -e

# 必須ディレクトリの事前作成（tmpfs は初回空のため）
mkdir -p /var/run/ws 2>/dev/null || true      # agent.sock 配置先
mkdir -p "$HOME/.claude" 2>/dev/null || true   # CLI 設定ディレクトリ
mkdir -p "/tmp/claude-$(id -u)" 2>/dev/null || true  # CLI スクラッチパッド
mkdir -p /workspace/.tmp 2>/dev/null || true   # サンドボックド bash 用 TMPDIR
mkdir -p /workspace/.claude 2>/dev/null || true  # プロジェクト設定

# socat: コンテナ内 TCP:8080 → Unix Socket /var/run/ws/proxy.sock
# pip/npm/curl/SDK CLI は HTTP_PROXY=http://127.0.0.1:8080 で利用
socat TCP-LISTEN:8080,fork,bind=127.0.0.1,reuseaddr UNIX-CONNECT:/var/run/ws/proxy.sock &
SOCAT_PID=$!

# シグナルハンドラ: socat も含めてクリーンアップ
cleanup() {
    kill "$SOCAT_PID" 2>/dev/null || true
    wait "$SOCAT_PID" 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

# workspace agent 起動 (foreground)
exec python -m workspace_agent.main
