#!/bin/sh
# workspace-base エントリポイント
# 1. socat TCP→UDS リバースプロキシを起動 (background)
# 2. workspace_agent を起動 (foreground)

set -e

# ランタイムディレクトリ作成（ReadonlyRootfs + tmpfs のため）
mkdir -p /var/run/ws 2>/dev/null || true
mkdir -p "${HOME:-/home/appuser}/.claude" 2>/dev/null || true
mkdir -p "/tmp/claude-$(id -u)" 2>/dev/null || true

# 書き込み権限の検証 — tmpfs の uid/gid 設定ミスを即座に検出
_writability_ok=true
for _dir in "${HOME:-/home/appuser}" "${HOME:-/home/appuser}/.claude" /workspace /tmp; do
    if [ -d "$_dir" ] && ! touch "$_dir/.writability_check" 2>/dev/null; then
        echo "FATAL: $_dir is not writable by $(id)" >&2
        _writability_ok=false
    else
        rm -f "$_dir/.writability_check" 2>/dev/null
    fi
done
if [ "$_writability_ok" = "false" ]; then
    echo "FATAL: Critical directories not writable. Check tmpfs uid/gid in container config." >&2
fi

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
