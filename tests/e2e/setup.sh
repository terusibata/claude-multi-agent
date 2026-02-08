#!/bin/bash
# E2E テスト環境セットアップスクリプト
#
# 使用方法:
#   ./tests/e2e/setup.sh          # セットアップ + テスト実行
#   ./tests/e2e/setup.sh --setup  # セットアップのみ
#   ./tests/e2e/setup.sh --clean  # クリーンアップのみ
#
# 前提条件:
#   - Docker デーモンが起動中であること
#   - docker-compose がインストール済みであること

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# --- 設定 ---
WORKSPACE_IMAGE="workspace-base:latest"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"

# --- 関数 ---

setup() {
    echo "=== E2E テスト環境セットアップ ==="

    # 1. workspace-base イメージのビルド
    echo "[1/3] workspace-base イメージビルド..."
    docker build -t "${WORKSPACE_IMAGE}" -f "${PROJECT_ROOT}/workspace-base/Dockerfile" "${PROJECT_ROOT}"
    echo "  OK: ${WORKSPACE_IMAGE}"

    # 2. Docker Compose でバックエンドサービス起動
    echo "[2/3] Docker Compose 起動..."
    docker compose -f "${COMPOSE_FILE}" up -d --wait postgres redis
    echo "  OK: postgres, redis"

    # 3. テスト用ディレクトリ準備
    echo "[3/3] テスト用ディレクトリ準備..."
    mkdir -p /var/run/workspace-sockets
    echo "  OK"

    echo ""
    echo "=== セットアップ完了 ==="
}

run_tests() {
    echo ""
    echo "=== E2E テスト実行 ==="
    cd "${PROJECT_ROOT}"
    python -m pytest tests/e2e/ -v --timeout=120 "$@"
    echo ""
    echo "=== テスト完了 ==="
}

cleanup() {
    echo ""
    echo "=== クリーンアップ ==="

    # テスト用コンテナを停止・削除
    echo "[1/3] テスト用ワークスペースコンテナ停止..."
    docker ps -q --filter "label=workspace=true" | xargs -r docker rm -f 2>/dev/null || true
    echo "  OK"

    # Docker Compose サービス停止
    echo "[2/3] Docker Compose 停止..."
    docker compose -f "${COMPOSE_FILE}" down 2>/dev/null || true
    echo "  OK"

    # ソケットディレクトリクリーンアップ
    echo "[3/3] ソケットディレクトリクリーンアップ..."
    rm -rf /var/run/workspace-sockets/* 2>/dev/null || true
    echo "  OK"

    echo ""
    echo "=== クリーンアップ完了 ==="
}

# --- メイン ---

case "${1:-}" in
    --setup)
        setup
        ;;
    --clean)
        cleanup
        ;;
    --help)
        echo "Usage: $0 [--setup|--clean|--help]"
        echo ""
        echo "  (no args)  セットアップ → テスト実行 → クリーンアップ"
        echo "  --setup    セットアップのみ"
        echo "  --clean    クリーンアップのみ"
        echo "  --help     このヘルプを表示"
        ;;
    *)
        setup
        run_tests "${@:2}"
        cleanup
        ;;
esac
