#!/bin/bash
# S3 ライフサイクルポリシー適用スクリプト
#
# 使用方法:
#   ./deployment/s3/apply-lifecycle.sh <bucket-name>
#
# 前提条件:
#   - AWS CLI がインストール・設定済みであること
#   - S3バケットが作成済みであること
#   - バケットバージョニングが有効であること（非最新バージョン削除ルールに必要）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY_FILE="${SCRIPT_DIR}/lifecycle-policy.json"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <bucket-name>"
    echo "Example: $0 my-workspace-bucket"
    exit 1
fi

BUCKET_NAME="$1"

echo "=== S3 ライフサイクルポリシー適用 ==="
echo "バケット: ${BUCKET_NAME}"
echo "ポリシー: ${POLICY_FILE}"
echo ""

# バケットの存在確認
echo "[1/4] バケット存在確認..."
if ! aws s3api head-bucket --bucket "${BUCKET_NAME}" 2>/dev/null; then
    echo "ERROR: バケット '${BUCKET_NAME}' が存在しないか、アクセス権がありません"
    exit 1
fi
echo "  OK"

# バージョニング有効化
echo "[2/4] バージョニング有効化..."
aws s3api put-bucket-versioning \
    --bucket "${BUCKET_NAME}" \
    --versioning-configuration Status=Enabled
echo "  OK"

# ライフサイクルポリシー適用
echo "[3/4] ライフサイクルポリシー適用..."
aws s3api put-bucket-lifecycle-configuration \
    --bucket "${BUCKET_NAME}" \
    --lifecycle-configuration "file://${POLICY_FILE}"
echo "  OK"

# 適用確認
echo "[4/4] 適用確認..."
aws s3api get-bucket-lifecycle-configuration --bucket "${BUCKET_NAME}"
echo ""

echo "=== 完了 ==="
echo ""
echo "適用されたルール:"
echo "  - 非最新バージョン: 30日後に削除"
echo "  - 90日アクセスなし: Glacier に移行"
echo "  - Glacier 移行後 180日 (通算270日): 削除"
