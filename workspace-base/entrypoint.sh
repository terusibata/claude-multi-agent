#!/bin/sh
# workspace-base エントリポイント
# 1. socat TCP→UDS リバースプロキシを起動 (background)
# 2. workspace_agent を起動 (foreground)

set -e

PROXY_SOCK="/var/run/ws/proxy.sock"

# 診断ログ: proxy.sock のステータスを出力
# WarmPoolコンテナでは起動時にproxy.sockが存在しない（ホスト側Proxyが
# 後から起動するため）。socatのforkモードは接続ごとに再試行するため問題ない。
if [ -S "${PROXY_SOCK}" ]; then
    echo "entrypoint: proxy.sock exists at startup"
    ls -la "${PROXY_SOCK}" 2>/dev/null || true
else
    echo "entrypoint: proxy.sock not yet available (will connect on demand via socat fork)"
fi

# socat: コンテナ内 TCP:8080 → Unix Socket /var/run/ws/proxy.sock
# pip/npm/curl/SDK CLI は HTTP_PROXY=http://127.0.0.1:8080 で利用
socat TCP-LISTEN:8080,fork,bind=127.0.0.1,reuseaddr UNIX-CONNECT:"${PROXY_SOCK}" &
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
