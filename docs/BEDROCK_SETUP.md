# AWS Bedrock セットアップガイド

Claude Agent SDKをAWS Bedrockで使用するための設定ガイドです。

## 必要なIAM権限

Claude Agent SDKがBedrockと正常に通信するには、以下の**3つの権限**が必要です：

1. `bedrock:InvokeModel` - モデルの呼び出し
2. `bedrock:InvokeModelWithResponseStream` - ストリーミングレスポンス
3. **`bedrock:ListInferenceProfiles`** - 推論プロファイルの一覧取得（重要！）

## IAMポリシーの設定

### 方法1: マネージドポリシーを使用（推奨）

IAMユーザーまたはロールに以下のマネージドポリシーをアタッチ：

```
AmazonBedrockFullAccess
```

### 方法2: カスタムポリシーを作成

より細かい権限制御が必要な場合は、以下のカスタムポリシーを作成：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListInferenceProfiles"
      ],
      "Resource": [
        "arn:aws:bedrock:*:*:inference-profile/*",
        "arn:aws:bedrock:*:*:application-inference-profile/*"
      ]
    }
  ]
}
```

## 環境変数の設定

`.env`ファイルに以下の環境変数を設定してください：

```bash
# AWS Bedrock設定
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-west-2  # または使用するリージョン
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key

# オプション: 一時認証情報を使用する場合
# AWS_SESSION_TOKEN=your_session_token
```

## トラブルシューティング

### エラー: "Command failed with exit code 1"

**原因**: IAM権限が不足している可能性が高いです。

**対処法**:
1. IAMユーザー/ロールに上記の3つの権限が付与されているか確認
2. 特に`bedrock:ListInferenceProfiles`権限が見落とされやすいので要確認
3. アプリケーションログで権限チェック結果を確認：

```bash
docker compose logs -f backend | grep "Bedrock権限"
```

### エラー: "database 'aiagent' does not exist"

**原因**: PostgreSQLのヘルスチェックが誤ったデータベース名をチェックしています。

**対処法**: このエラーは無害です。アプリケーションは正しく`aiagent_db`に接続しています。

### デバッグモードの有効化

詳細なログを確認するには、`docker-compose.yml`で以下の環境変数を設定：

```yaml
environment:
  DEBUG: "1"
  CLAUDE_CODE_LOG_LEVEL: "debug"
```

## 権限チェックツールの使用

アプリケーション起動時、開発環境では自動的にBedrock権限がチェックされます。

ログ出力例:

```
INFO: Bedrock権限チェック実行中...
INFO: ✓ bedrock:ListInferenceProfiles 権限OK
INFO: ✓ bedrock:InvokeModel 権限OK
INFO: Bedrock権限チェック完了: OK
```

権限エラーがある場合:

```
ERROR: Bedrock権限エラー: Bedrock権限が不足しています
ERROR:   推奨: bedrock:ListInferenceProfiles 権限が不足しています
ERROR:   推奨: IAMポリシーに以下の権限を追加してください：
ERROR:   推奨: - AmazonBedrockFullAccess (マネージドポリシー)
```

## 関連リンク

- [Claude Agent SDK - GitHub Issue #224](https://github.com/anthropics/claude-agent-sdk-python/issues/224)
- [AWS Bedrock IAM権限](https://docs.aws.amazon.com/bedrock/latest/userguide/security_iam_id-based-policy-examples.html)
- [Claude Agent SDK Documentation](https://platform.claude.com/docs/en/agent-sdk/overview)
