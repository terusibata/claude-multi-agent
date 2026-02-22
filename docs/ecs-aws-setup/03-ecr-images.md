# 3. ECR リポジトリ・コンテナイメージ

## 3.1 ECR リポジトリ作成

2 つのコンテナイメージ用にリポジトリを作成する。

```bash
REGION=ap-northeast-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# workspace-agent 用
aws ecr create-repository \
  --repository-name ai-agent/workspace-base \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256

# proxy-sidecar 用
aws ecr create-repository \
  --repository-name ai-agent/proxy-sidecar \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256
```

### ライフサイクルポリシー（推奨）

古いイメージを自動削除する:

```bash
aws ecr put-lifecycle-policy \
  --repository-name ai-agent/workspace-base \
  --lifecycle-policy-text '{
    "rules": [
      {
        "rulePriority": 1,
        "description": "Keep last 10 images",
        "selection": {
          "tagStatus": "any",
          "countType": "imageCountMoreThan",
          "countNumber": 10
        },
        "action": { "type": "expire" }
      }
    ]
  }'
```

## 3.2 イメージビルド

プロジェクトルートからビルドする。

### workspace-base（workspace-agent コンテナ）

```bash
# ビルド
docker build \
  -t ai-agent/workspace-base:latest \
  -f workspace-base/Dockerfile \
  .
```

このイメージには以下が含まれる:

| コンポーネント | 説明 |
|---------------|------|
| Python 3.11 + venv | エージェント実行環境 |
| Node.js 20 | Claude Agent SDK CLI が内部で使用 |
| socat | UDS↔TCP ブリッジ（Docker モード用、ECS では不使用） |
| claude-agent-sdk | Claude Code CLI バイナリ（standalone ELF） |
| workspace_agent/ | FastAPI アプリ（`/execute`, `/exec`, `/health`） |
| entrypoint.sh | 起動スクリプト（UDS/HTTP モード自動切替） |

**ポート**: 9000 (HTTP)
**ヘルスチェック**: `curl http://localhost:9000/health`
**実行ユーザー**: appuser (UID 1000)

### proxy-sidecar（プロキシサイドカーコンテナ）

```bash
# ビルド
docker build \
  -t ai-agent/proxy-sidecar:latest \
  -f workspace-base/Dockerfile.proxy-sidecar \
  .
```

このイメージには以下が含まれる:

| コンポーネント | 説明 |
|---------------|------|
| Python 3.11 + venv | プロキシ実行環境 |
| httpx, boto3, botocore | HTTP クライアント + AWS SDK |
| Credential Injection Proxy | SigV4 署名注入 + ドメインホワイトリスト |
| ProxyAdminServer | MCP ヘッダールール動的更新 |

**ポート**: 8080 (Forward Proxy), 8081 (Admin HTTP)
**ヘルスチェック**: `curl http://localhost:8081/health`
**実行ユーザー**: appuser (UID 1000)

## 3.3 ECR へプッシュ

```bash
REGION=ap-northeast-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# ECR ログイン
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $ECR_URI

# workspace-base
docker tag ai-agent/workspace-base:latest \
  $ECR_URI/ai-agent/workspace-base:latest
docker push $ECR_URI/ai-agent/workspace-base:latest

# proxy-sidecar
docker tag ai-agent/proxy-sidecar:latest \
  $ECR_URI/ai-agent/proxy-sidecar:latest
docker push $ECR_URI/ai-agent/proxy-sidecar:latest
```

## 3.4 マルチアーキテクチャビルド（オプション）

Fargate で ARM64 (Graviton) を使用する場合:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t $ECR_URI/ai-agent/workspace-base:latest \
  -f workspace-base/Dockerfile \
  --push .
```

**注意**: Claude Agent SDK の CLI バイナリが対象アーキテクチャに対応していることを確認すること。
