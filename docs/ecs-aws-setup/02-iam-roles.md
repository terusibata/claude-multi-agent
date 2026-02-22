# 2. IAM ロール・ポリシー

ECS モードでは 3 つの IAM ロールが必要。

## 2.1 ロール一覧

| ロール | 用途 | 使用者 |
|--------|------|--------|
| ECS タスク実行ロール | ECR イメージプル、CloudWatch Logs 書き込み | ECS エージェント（AWS 側） |
| ECS タスクロール | Bedrock API アクセス（プロキシサイドカー用） | タスク内コンテナ |
| Backend 実行ロール | ECS API、CloudWatch Logs 読み取り、S3、Bedrock | Backend アプリケーション |

## 2.2 ECS タスク実行ロール

ECS エージェントがタスク起動時に使用するロール。ECR からのイメージプルと CloudWatch Logs への書き込みに必要。

### 信頼ポリシー

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### 権限ポリシー

AWS 管理ポリシー `AmazonECSTaskExecutionRolePolicy` をアタッチする（ECR プル + CloudWatch Logs 書き込みをカバー）。

```bash
# ロール作成
aws iam create-role \
  --role-name ai-agent-ecs-task-execution-role \
  --assume-role-policy-document file://trust-policy-ecs-tasks.json

# AWS管理ポリシーをアタッチ
aws iam attach-role-policy \
  --role-name ai-agent-ecs-task-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

## 2.3 ECS タスクロール

タスク内のコンテナ（proxy-sidecar）が AWS API にアクセスするためのロール。
Bedrock API の SigV4 署名に使用される。

### 信頼ポリシー

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### 権限ポリシー

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*"
      ]
    }
  ]
}
```

```bash
# ロール作成
aws iam create-role \
  --role-name ai-agent-ecs-task-role \
  --assume-role-policy-document file://trust-policy-ecs-tasks.json

# カスタムポリシー作成・アタッチ
aws iam create-policy \
  --policy-name ai-agent-bedrock-access \
  --policy-document file://bedrock-policy.json

aws iam attach-role-policy \
  --role-name ai-agent-ecs-task-role \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/ai-agent-bedrock-access
```

**補足**: proxy-sidecar は `AWSCredentials` を設定から受け取る方式と、IAM タスクロール経由の方式がある。タスクロールを使用する場合は `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` の環境変数は不要（boto3 が自動的にタスクロールの一時認証情報を使用する）。

## 2.4 Backend 実行ロール

Backend アプリケーション（FastAPI）が使用するロール。ECS タスクの管理、CloudWatch Logs の読み取り、S3 ワークスペース操作、Bedrock API アクセスに必要。

### 必要な権限

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECSTaskManagement",
      "Effect": "Allow",
      "Action": [
        "ecs:RunTask",
        "ecs:StopTask",
        "ecs:DescribeTasks",
        "ecs:ListTasks"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "ecs:cluster": "arn:aws:ecs:<REGION>:<ACCOUNT_ID>:cluster/<CLUSTER_NAME>"
        }
      }
    },
    {
      "Sid": "PassRoleForECS",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::<ACCOUNT_ID>:role/ai-agent-ecs-task-execution-role",
        "arn:aws:iam::<ACCOUNT_ID>:role/ai-agent-ecs-task-role"
      ]
    },
    {
      "Sid": "CloudWatchLogsRead",
      "Effect": "Allow",
      "Action": [
        "logs:GetLogEvents"
      ],
      "Resource": [
        "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/ecs/workspace-agent:*"
      ]
    },
    {
      "Sid": "S3WorkspaceAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::<BUCKET_NAME>",
        "arn:aws:s3:::<BUCKET_NAME>/*"
      ]
    },
    {
      "Sid": "BedrockAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*"
      ]
    }
  ]
}
```

```bash
aws iam create-policy \
  --policy-name ai-agent-backend-policy \
  --policy-document file://backend-policy.json
```

### Backend のデプロイ形態ごとの適用方法

| デプロイ形態 | ロール設定方法 |
|-------------|---------------|
| ECS Fargate/EC2 | タスクロールとしてアタッチ |
| EC2 インスタンス | インスタンスプロファイルとしてアタッチ |
| EKS | ServiceAccount の IAM Roles for Service Accounts (IRSA) |

## 2.5 権限の最小化に関する注意事項

- `ecs:RunTask` の `Resource` はタスク定義 ARN に限定可能:
  ```
  "Resource": "arn:aws:ecs:<REGION>:<ACCOUNT_ID>:task-definition/workspace-agent:*"
  ```
- `ecs:StopTask` / `ecs:DescribeTasks` はタスク ARN に `Condition` で限定可能
- S3 は `s3:GetObject` / `s3:PutObject` のみに限定し、`s3:DeleteObject` は必要な場合のみ追加
- Bedrock は使用するモデルの ARN に限定可能
