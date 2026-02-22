# 4. ECS クラスター・タスク定義

## 4.1 ECS クラスター作成

```bash
aws ecs create-cluster \
  --cluster-name ai-agent-cluster \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=FARGATE,weight=1,base=0 \
  --setting name=containerInsights,value=enabled
```

**Fargate vs EC2**:

| 項目 | Fargate | EC2 |
|------|---------|-----|
| 運用負荷 | 低（サーバーレス） | 高（インスタンス管理必要） |
| 起動速度 | 30-60秒 | 10-30秒（予めインスタンス起動） |
| コスト | 高い（秒課金） | 低い（Reserved Instance 活用可） |
| GPU | 不可 | 可 |
| 推奨用途 | 初期導入・小規模 | 大規模・コスト最適化 |

EC2 の場合は `ECS_CAPACITY_PROVIDER` 環境変数に Capacity Provider 名を設定する。

## 4.2 タスク定義

### 完全なタスク定義 JSON

以下のJSONを `task-definition.json` として保存する。`<PLACEHOLDER>` の値はすべて環境に合わせて置換すること。

```json
{
  "family": "workspace-agent",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "executionRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/ai-agent-ecs-task-execution-role",
  "taskRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/ai-agent-ecs-task-role",
  "containerDefinitions": [
    {
      "name": "workspace-agent",
      "image": "<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/ai-agent/workspace-base:latest",
      "essential": true,
      "portMappings": [
        {
          "containerPort": 9000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        { "name": "AGENT_LISTEN_MODE", "value": "http" },
        { "name": "AGENT_HTTP_PORT", "value": "9000" },
        { "name": "HTTP_PROXY", "value": "http://127.0.0.1:8080" },
        { "name": "HTTPS_PROXY", "value": "http://127.0.0.1:8080" },
        { "name": "NO_PROXY", "value": "localhost,127.0.0.1" },
        { "name": "CLAUDE_CODE_USE_BEDROCK", "value": "1" },
        { "name": "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "value": "1" },
        { "name": "AWS_REGION", "value": "<REGION>" },
        { "name": "ANTHROPIC_BEDROCK_BASE_URL", "value": "http://127.0.0.1:8080" },
        { "name": "HOME", "value": "/home/appuser" },
        { "name": "CLAUDE_CONFIG_DIR", "value": "/home/appuser/.claude" },
        { "name": "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "value": "1" },
        { "name": "NODE_OPTIONS", "value": "" }
      ],
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -sf http://localhost:9000/health || exit 1"],
        "interval": 30,
        "timeout": 10,
        "retries": 3,
        "startPeriod": 60
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/workspace-agent",
          "awslogs-region": "<REGION>",
          "awslogs-stream-prefix": "agent",
          "awslogs-create-group": "true"
        }
      },
      "linuxParameters": {
        "initProcessEnabled": true
      }
    },
    {
      "name": "proxy-sidecar",
      "image": "<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/ai-agent/proxy-sidecar:latest",
      "essential": false,
      "portMappings": [
        {
          "containerPort": 8080,
          "protocol": "tcp"
        },
        {
          "containerPort": 8081,
          "protocol": "tcp"
        }
      ],
      "environment": [
        { "name": "PROXY_PORT", "value": "8080" },
        { "name": "PROXY_ADMIN_PORT", "value": "8081" },
        { "name": "AWS_REGION", "value": "<REGION>" },
        { "name": "PROXY_DOMAIN_WHITELIST", "value": "pypi.org,files.pythonhosted.org,registry.npmjs.org,api.anthropic.com,bedrock-runtime.us-east-1.amazonaws.com,bedrock-runtime.us-west-2.amazonaws.com,bedrock-runtime.ap-northeast-1.amazonaws.com" },
        { "name": "PROXY_LOG_ALL_REQUESTS", "value": "true" }
      ],
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -sf http://localhost:8081/health || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 15
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/workspace-agent",
          "awslogs-region": "<REGION>",
          "awslogs-stream-prefix": "proxy",
          "awslogs-create-group": "true"
        }
      }
    }
  ],
  "tags": [
    { "key": "Application", "value": "ai-agent" },
    { "key": "Component", "value": "workspace" }
  ]
}
```

### タスク定義の登録

```bash
aws ecs register-task-definition \
  --cli-input-json file://task-definition.json
```

## 4.3 各パラメータの解説

### CPU / Memory の選定

| 同時ユーザー想定 | CPU | Memory | 備考 |
|----------------|-----|--------|------|
| 軽量 (テスト) | 1024 (1 vCPU) | 2048 (2 GB) | 最低限 |
| 標準 | 2048 (2 vCPU) | 4096 (4 GB) | 推奨 |
| 重量 (コード生成多) | 4096 (4 vCPU) | 8192 (8 GB) | 大規模ファイル操作時 |

**注意**: Fargate の有効な CPU/Memory の組み合わせには制約がある。
[AWS ドキュメント](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html) を参照。

### コンテナ別の設定ポイント

#### workspace-agent

| 設定 | 値 | 理由 |
|------|-----|------|
| `essential` | `true` | このコンテナが停止したらタスク全体を停止 |
| `AGENT_LISTEN_MODE` | `http` | ECS では UDS ではなく HTTP リスン |
| `HTTP_PROXY` | `http://127.0.0.1:8080` | SDK CLI の外部通信をサイドカーのプロキシ経由に |
| `ANTHROPIC_BEDROCK_BASE_URL` | `http://127.0.0.1:8080` | Bedrock API もプロキシ経由（SigV4 署名注入） |
| `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK` | `1` | ネットワーク制限環境でのバージョンチェックスキップ |
| `NODE_OPTIONS` | `""` | CLI バイナリ（standalone ELF）の動作に不要な Node.js オプションをクリア |
| `startPeriod` | `60` | SDK CLI の初回起動に時間がかかるため |

#### proxy-sidecar

| 設定 | 値 | 理由 |
|------|-----|------|
| `essential` | `false` | プロキシ障害時でもタスクは維持（Backend が復旧処理） |
| `PROXY_DOMAIN_WHITELIST` | カンマ区切りドメイン | 許可する外部ドメインを制限 |
| `startPeriod` | `15` | プロキシは高速に起動するため短め |

### ドメインホワイトリスト

workspace-agent から外部通信できるドメインを制限する。proxy-sidecar がホワイトリストに基づいてフィルタリングする。

| ドメイン | 用途 |
|---------|------|
| `pypi.org` | Python パッケージインストール |
| `files.pythonhosted.org` | Python パッケージダウンロード |
| `registry.npmjs.org` | npm パッケージインストール |
| `api.anthropic.com` | Anthropic API（直接アクセス時） |
| `bedrock-runtime.*.amazonaws.com` | AWS Bedrock API |

Bedrock のリージョンは使用するリージョンに応じて追加すること。

## 4.4 タスク定義の更新

イメージを更新した場合はタスク定義の新リビジョンを登録する:

```bash
# 新リビジョンの登録
aws ecs register-task-definition \
  --cli-input-json file://task-definition.json

# Backend の環境変数 ECS_TASK_DEFINITION を更新
# 例: "workspace-agent:3" (family:revision) または ARN
```

Backend の `ECS_TASK_DEFINITION` は以下の形式で指定可能:

| 形式 | 例 | 動作 |
|------|-----|------|
| family名 | `workspace-agent` | 最新リビジョンを自動使用 |
| family:revision | `workspace-agent:3` | 特定リビジョンを固定 |
| 完全 ARN | `arn:aws:ecs:...` | 特定リビジョンを固定 |
