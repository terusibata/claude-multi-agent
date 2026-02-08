# S3 ライフサイクルポリシー

## 概要

ワークスペースファイルの長期保管コスト最適化のための S3 ライフサイクルポリシーです。

## ルール

| ルール | 対象 | 動作 |
|--------|------|------|
| 非最新バージョン削除 | `workspaces/` プレフィックス | 30日経過後に自動削除 |
| Glacier 移行 | `workspaces/` プレフィックス | 90日アクセスなし → Glacier に移行 |
| 自動削除 | `workspaces/` プレフィックス | 270日（Glacier移行後180日）で削除 |

## 前提条件

- AWS CLI がインストール・設定済みであること
- S3 バケットが作成済みであること
- `aws configure` で適切な認証情報が設定されていること

## 適用手順

```bash
# 1. ライフサイクルポリシーを適用（バージョニング有効化含む）
./deployment/s3/apply-lifecycle.sh <bucket-name>

# 例:
./deployment/s3/apply-lifecycle.sh my-workspace-bucket
```

スクリプトは以下を自動実行します:

1. バケット存在確認
2. バケットバージョニング有効化
3. ライフサイクルポリシー適用 (`lifecycle-policy.json`)
4. 適用結果の確認出力

## 適用確認

```bash
aws s3api get-bucket-lifecycle-configuration --bucket <bucket-name>
```

## ファイル構成

| ファイル | 説明 |
|---------|------|
| `lifecycle-policy.json` | ライフサイクルルール定義 (JSON) |
| `apply-lifecycle.sh` | AWS CLI による適用スクリプト |

## 注意事項

- バージョニング有効化は不可逆（Suspended には変更可能だが無効化は不可）
- Glacier からの復元には数時間〜数日かかるため、頻繁にアクセスするファイルには不向き
- 本番環境への適用前に、ステージング環境でテストすることを推奨
