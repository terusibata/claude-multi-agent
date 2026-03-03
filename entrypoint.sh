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
if ! gosu appuser alembic upgrade head 2>&1; then
    echo "Migration failed. Checking for stale alembic version..."
    # マイグレーションファイルが統合された場合、DBに古いリビジョンが残っている可能性がある。
    # 現在のheadでスタンプし直してリトライする。
    echo "Stamping database with current head revision..."
    gosu appuser alembic stamp head
    echo "Retrying migrations..."
    gosu appuser alembic upgrade head
fi

echo "Starting application..."
exec gosu appuser "$@"
