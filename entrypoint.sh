#!/bin/bash
set -e

# =============================================
# Phase 1: root権限での初期化（ソケットディレクトリ等）
# =============================================

# ワークスペースSocket用ディレクトリの作成・権限修正
# docker-composeのバインドマウントはホスト側ディレクトリをroot:rootで作成するため、
# appuser (UID 1000) が書き込めるよう権限を設定する
SOCKET_DIR="${WORKSPACE_SOCKET_BASE_PATH:-/var/run/workspace-sockets}"
mkdir -p "$SOCKET_DIR"
chown appuser:appuser "$SOCKET_DIR"
chmod 0755 "$SOCKET_DIR"

# =============================================
# Phase 2: appuserとしてアプリケーション起動
# =============================================

echo "Running database migrations..."
gosu appuser alembic upgrade head

echo "Starting application..."
exec gosu appuser "$@"
