# AWS IAM セットアップ手順書

## 命名規則

本手順で使用するリソース名は以下の通りです。

| リソース | 名前 |
|---------|------|
| IAMユーザー（バックエンド用） | `claude-agent-backend` |
| IAMポリシー（バックエンド用） | `ClaudeAgentBackendPolicy` |
| IAMロール（AgentCore用） | `AgentCoreExecutionRole` |
| IAMポリシー（AgentCore用） | `AgentCoreExecutionPolicy` |
| S3バケット | `claude-multi-agent-workspaces`（※ 任意の名前に変更可） |
| ECRリポジトリ | `workspace-agent` |
| AgentCore Runtime | `workspace-agent` |
| リージョン | `ap-northeast-1` |

> **⚠️ 注意**: 以下の手順中の `123456789012` はすべて自分のAWSアカウントIDに置き換えてください。アカウントIDはコンソール右上のアカウント名をクリックすると確認できます。

---

## STEP 1 ― S3バケットの作成

1. AWSマネジメントコンソールにログイン
2. 上部の検索バーに **S3** と入力 → **S3** を選択
3. **バケットを作成** をクリック
4. 以下を入力

| 項目 | 値 |
|------|-----|
| バケット名 | `claude-multi-agent-workspaces` |
| AWSリージョン | `ap-northeast-1` |
| オブジェクト所有者 | ACL無効（推奨）のまま |
| パブリックアクセスをすべてブロック | ✅ チェックのまま |

5. その他はデフォルトのまま → **バケットを作成**

---

## STEP 2 ― ECRリポジトリの作成

1. 上部の検索バーに **ECR** と入力 → **Elastic Container Registry** を選択
2. 左メニュー → **リポジトリ** → **リポジトリを作成**
3. 以下を入力

| 項目 | 値 |
|------|-----|
| 可視性設定 | プライベート |
| リポジトリ名 | `workspace-agent` |

4. その他はデフォルトのまま → **リポジトリを作成**
5. 作成後、リポジトリのURIを控えておく（`123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/workspace-agent`）

---

## STEP 3 ― バックエンド用IAMポリシーの作成

バックエンド（FastAPI）が使用する権限です。

1. 上部の検索バーに **IAM** と入力 → **IAM** を選択
2. 左メニュー → **ポリシー** → **ポリシーを作成**
3. 上部の **JSON** タブをクリック
4. エディタの中身をすべて削除し、以下を貼り付ける

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AgentCoreAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime"
      ],
      "Resource": "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:*"
    },
    {
      "Sid": "S3WorkspaceAccess",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::claude-multi-agent-workspaces",
        "arn:aws:s3:::claude-multi-agent-workspaces/*"
      ]
    },
    {
      "Sid": "ECRAuth",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ECRPush",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:PutImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload"
      ],
      "Resource": "arn:aws:ecr:ap-northeast-1:123456789012:repository/workspace-agent"
    }
  ]
}
```

5. **次へ** をクリック
6. ポリシー名に `ClaudeAgentBackendPolicy` と入力
7. 説明に `Claude Multi-Agent Backend used` と入力
8. **ポリシーを作成** をクリック

---

## STEP 4 ― バックエンド用IAMユーザーの作成

1. 左メニュー → **ユーザー** → **ユーザーを作成**
2. ユーザー名に `claude-agent-backend` と入力
3. 「AWS マネジメントコンソールへのユーザーアクセスを提供する」は **チェックしない**（プログラムアクセスのみ）
4. **次へ** をクリック

### 許可の設定

5. **ポリシーを直接アタッチする** を選択
6. 検索窓に `ClaudeAgentBackendPolicy` と入力
7. 表示されたポリシーに ✅ チェックを入れる
8. **次へ** をクリック
9. 内容を確認 → **ユーザーを作成**

### アクセスキーの発行

10. 作成されたユーザー名 `claude-agent-backend` をクリック
11. **セキュリティ認証情報** タブを選択
12. 「アクセスキー」セクション → **アクセスキーを作成**
13. ユースケースで **コマンドラインインターフェイス (CLI)** を選択
14. 下部の確認チェックボックスに ✅ チェック → **次へ**
15. 説明タグ（任意）を入力 → **アクセスキーを作成**
16. 表示される **アクセスキーID** と **シークレットアクセスキー** をコピーして安全に保管

> 🔑 **この画面を閉じるとシークレットアクセスキーは二度と表示されません。** 必ずこの時点で控えてください。

---

## STEP 5 ― AgentCore Execution Role 用ポリシーの作成

AgentCore Runtime上のコンテナが使用する権限です。

1. 左メニュー → **ポリシー** → **ポリシーを作成**
2. **JSON** タブをクリック
3. 以下を貼り付ける

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECRImageAccess",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "arn:aws:ecr:ap-northeast-1:123456789012:repository/workspace-agent"
    },
    {
      "Sid": "ECRTokenAccess",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "BedrockModelInvocation",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListFoundationModels"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3WorkspaceAccess",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::claude-multi-agent-workspaces",
        "arn:aws:s3:::claude-multi-agent-workspaces/*"
      ]
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:DescribeLogGroups"
      ],
      "Resource": "arn:aws:logs:ap-northeast-1:123456789012:log-group:*"
    },
    {
      "Sid": "CloudWatchLogStreams",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:ap-northeast-1:123456789012:log-group:/aws/bedrock-agentcore/runtimes/*:*"
    },
    {
      "Sid": "XRayTracing",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords",
        "xray:GetSamplingRules",
        "xray:GetSamplingTargets"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "cloudwatch:namespace": "bedrock-agentcore"
        }
      }
    },
    {
      "Sid": "MarketplaceSubscription",
      "Effect": "Allow",
      "Action": [
        "aws-marketplace:ViewSubscriptions",
        "aws-marketplace:Subscribe"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
            "aws:CalledViaLast": "bedrock.amazonaws.com"
        }
      }
    }
  ]
}
```

4. **次へ** をクリック
5. ポリシー名に `AgentCoreExecutionPolicy` と入力
6. 説明に `AgentCore Runtime container policy` と入力
7. **ポリシーを作成** をクリック

---

## STEP 6 ― AgentCore Execution Role の作成

1. 左メニュー → **ロール** → **ロールを作成**
2. 信頼されたエンティティタイプ → **カスタム信頼ポリシー** を選択
3. JSONエディタに以下を貼り付ける

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "123456789012"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:*"
        }
      }
    }
  ]
}
```

4. **次へ** をクリック

### 許可ポリシーのアタッチ

5. 検索窓に `AgentCoreExecutionPolicy` と入力
6. 表示されたポリシーに ✅ チェックを入れる
7. **次へ** をクリック
8. ロール名に `AgentCoreExecutionRole` と入力
9. 説明に `AgentCore Runtime Role` と入力
10. **ロールを作成** をクリック

### ロールARNの確認

11. 作成後、ロール一覧から `AgentCoreExecutionRole` をクリック
12. 上部に表示される **ARN** をコピー（`arn:aws:iam::123456789012:role/AgentCoreExecutionRole`）

---

## STEP 7 ― 動作確認用のAWS CLI設定

ローカルPCで以下を実行して、STEP 4 で取得したアクセスキーを設定します。

```bash
aws configure
```

| プロンプト | 入力値 |
|-----------|--------|
| AWS Access Key ID | STEP 4で取得したアクセスキーID |
| AWS Secret Access Key | STEP 4で取得したシークレットアクセスキー |
| Default region name | `ap-northeast-1` |
| Default output format | `json` |

### 接続テスト

```bash
# IAMユーザーの確認
aws sts get-caller-identity

# S3バケットの確認
aws s3 ls s3://claude-multi-agent-workspaces/

# ECRログイン確認
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com

# Bedrockモデル一覧の確認
aws bedrock list-foundation-models --region ap-northeast-1 --query "modelSummaries[?contains(modelId, 'claude')].[modelId]" --output table
```

---

## STEP 8 ― コンテナビルド & AgentCore Runtime 作成

```bash
# 1. コンテナイメージをビルド（ARM64）
docker build --platform linux/arm64 -t workspace-agent:latest -f workspace-agent/Dockerfile .

# 2. ECRにタグ付け
docker tag workspace-agent:latest \
  123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/workspace-agent:latest

# 3. ECRにプッシュ
docker push 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/workspace-agent:latest

# 4. AgentCore Runtimeを作成（初回のみ）
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name workspace-agent \
  --agent-runtime-artifact '{"containerConfiguration": {"containerUri": "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/workspace-agent:latest"}}' \
  --network-configuration '{"networkMode": "PUBLIC"}' \
  --role-arn arn:aws:iam::123456789012:role/AgentCoreExecutionRole \
  --region ap-northeast-1
```

> 作成が成功すると `runtimeArn` が返されます。これを `.env` の `AGENTCORE_RUNTIME_ARN` に設定します。

---

## STEP 9 ― .env ファイルの設定

```bash
# 必須
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX        # STEP 4で取得
AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxx  # STEP 4で取得
AWS_REGION=ap-northeast-1
AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/xxxx  # STEP 8で取得
DATABASE_URL=postgresql+asyncpg://aiagent:aiagent_password@localhost:5432/aiagent
S3_BUCKET_NAME=claude-multi-agent-workspaces
API_KEYS=your-secure-api-key-here

# オプション
APP_ENV=development
LOG_LEVEL=INFO
```

---

## STEP 10 ― 起動 & 最終確認

```bash
# Docker Composeで起動
docker-compose up -d --build

# ヘルスチェック
curl http://localhost:8000/health

# テナント作成テスト
curl -X POST http://localhost:8000/api/tenants \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secure-api-key-here" \
  -H "X-Admin-ID: admin-001" \
  -d '{"name": "test-tenant"}'
```

---

## 全体構成図

```
┌─────────────────────────────────────────────────┐
│  IAMユーザー: claude-agent-backend               │
│  ├── ClaudeAgentBackendPolicy                   │
│  │   ├── Bedrock: InvokeModel                   │
│  │   ├── AgentCore: InvokeAgentRuntime          │
│  │   ├── S3: claude-multi-agent-workspaces (CRUD)      │
│  │   └── ECR: workspace-agent (Push/Pull)       │
│  └── アクセスキー → .env                         │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  IAMロール: AgentCoreExecutionRole               │
│  ├── 信頼: bedrock-agentcore.amazonaws.com       │
│  ├── AgentCoreExecutionPolicy                   │
│  │   ├── ECR: workspace-agent (Pull)            │
│  │   ├── Bedrock: InvokeModel                   │
│  │   ├── S3: claude-multi-agent-workspaces (Read/Write)│
│  │   ├── CloudWatch Logs                        │
│  │   ├── X-Ray                                  │
│  │   └── CloudWatch Metrics                     │
│  └── ARN → bedrock-agentcore-control create-agent-runtime │
└─────────────────────────────────────────────────┘
```

---

## 置換チェックリスト

手順中のすべての `123456789012` を自分のAWSアカウントIDに一括置換してください。

S3バケット名 `claude-multi-agent-workspaces` を変更する場合は、以下の4箇所すべてで置換が必要です。

1. STEP 1 ― S3バケット作成時のバケット名
2. STEP 3 ― `ClaudeAgentBackendPolicy` 内の S3 Resource
3. STEP 5 ― `AgentCoreExecutionPolicy` 内の S3 Resource
4. STEP 9 ― `.env` の `S3_BUCKET_NAME`