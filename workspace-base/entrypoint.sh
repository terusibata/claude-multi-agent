#!/bin/sh
# workspace-base エントリポイント
# 1. socat TCP→UDS リバースプロキシを起動 (background)
# 2. workspace_agent を起動 (foreground)

set -e

# ランタイムディレクトリ作成（ReadonlyRootfs + tmpfs のため）
mkdir -p /var/run/ws 2>/dev/null || true
mkdir -p "${HOME:-/home/appuser}/.claude" 2>/dev/null || true
mkdir -p "/tmp/claude-$(id -u)" 2>/dev/null || true

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
