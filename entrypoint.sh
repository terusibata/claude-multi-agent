#!/bin/bash
set -e

# =============================================
# Phase 1: Root権限での初期化
# =============================================
# gosu パターン: root で起動 → ランタイム初期化 → gosu で権限ドロップ

# =============================================
# Phase 2: appuser として起動
# =============================================
echo "Running database migrations..."
gosu appuser alembic upgrade head

echo "Starting application..."
exec gosu appuser "$@"
