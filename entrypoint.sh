#!/bin/bash
set -e

# =============================================
# Phase 1: Root権限での初期化
# =============================================
# PostgreSQL/Redis公式イメージと同パターン:
#   root で起動 → ランタイム初期化 → gosu で権限ドロップ

# --- Docker Socket GID 検出・同期 ---
# ホスト側の docker.sock GID はシステムにより異なる (999, 998, 133 等)。
# コンテナ内 docker グループの GID をランタイムで合わせることで、
# gosu の initgroups() が正しい補助グループを付与する。
DOCKER_SOCK="/var/run/docker.sock"
if [ -S "$DOCKER_SOCK" ]; then
    HOST_DOCKER_GID=$(stat -c '%g' "$DOCKER_SOCK")
    CURRENT_DOCKER_GID=$(getent group docker | cut -d: -f3)

    if [ "$HOST_DOCKER_GID" != "$CURRENT_DOCKER_GID" ]; then
        echo "Docker socket GID mismatch: host=${HOST_DOCKER_GID}, container=${CURRENT_DOCKER_GID}"

        # 同じ GID の既存グループがあるか確認
        EXISTING_GROUP=$(getent group "$HOST_DOCKER_GID" | cut -d: -f1 || true)
        if [ -z "$EXISTING_GROUP" ]; then
            # GID 未使用 → docker グループの GID を更新
            groupmod -g "$HOST_DOCKER_GID" docker
        else
            # 別グループが同 GID を使用 → appuser をそのグループに追加
            usermod -aG "$EXISTING_GROUP" appuser 2>/dev/null || true
        fi
        echo "Docker socket GID synchronized to ${HOST_DOCKER_GID}"
    fi
    # appuser が docker グループに所属していることを保証
    usermod -aG docker appuser 2>/dev/null || true
else
    echo "WARNING: Docker socket not found at ${DOCKER_SOCK}"
fi

# --- Workspace Sockets ディレクトリ権限修正 ---
# bind mount はホスト側ディレクトリを root:root で作成するため、
# appuser が書き込めるよう所有者を修正する。
SOCKET_DIR="${WORKSPACE_SOCKET_BASE_PATH:-/var/run/workspace-sockets}"
mkdir -p "$SOCKET_DIR"
chown appuser:appuser "$SOCKET_DIR"
chmod 0755 "$SOCKET_DIR"

# =============================================
# Phase 2: appuser として起動
# =============================================
echo "Running database migrations..."
gosu appuser alembic upgrade head

echo "Starting application..."
exec gosu appuser "$@"
