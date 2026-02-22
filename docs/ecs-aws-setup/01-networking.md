# 1. VPC・ネットワーク設定

## 概要

ECS タスクは **awsvpc** ネットワークモードで起動し、各タスクに専用の ENI（Elastic Network Interface）とプライベート IP が割り当てられる。Backend からタスクへの通信はプライベートサブネット内の TCP 通信のみ。

## 1.1 VPC

既存の VPC を使用するか、新規に作成する。

```bash
# 新規作成の場合
aws ec2 create-vpc \
  --cidr-block 10.0.0.0/16 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=ai-agent-vpc}]'
```

**要件**:
- DNS ホスト名解決: 有効
- DNS サポート: 有効

```bash
aws ec2 modify-vpc-attribute --vpc-id vpc-xxxxx --enable-dns-hostnames '{"Value":true}'
aws ec2 modify-vpc-attribute --vpc-id vpc-xxxxx --enable-dns-support '{"Value":true}'
```

## 1.2 サブネット

### プライベートサブネット（ECS タスク用）

ECS タスク（ワークスペースコンテナ）はプライベートサブネットに配置する。
**複数 AZ に配置**すること（可用性確保）。

```bash
# AZ-a
aws ec2 create-subnet \
  --vpc-id vpc-xxxxx \
  --cidr-block 10.0.10.0/24 \
  --availability-zone ap-northeast-1a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=ai-agent-ecs-private-a}]'

# AZ-c
aws ec2 create-subnet \
  --vpc-id vpc-xxxxx \
  --cidr-block 10.0.11.0/24 \
  --availability-zone ap-northeast-1c \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=ai-agent-ecs-private-c}]'
```

**注意**: サブネットの IP アドレス数は WarmPool の最大サイズ（デフォルト 120）以上を確保すること。`/24` なら 251 個の IP が使用可能で、`ECS_WARM_POOL_MAX_SIZE=120` に対して十分。複数サブネットに分散する場合は合計で確保する。

### NAT Gateway（外部通信用）

ワークスペースコンテナはプロキシ経由で外部通信（PyPI、npm 等）を行う。プロキシサイドカーからの外部通信には NAT Gateway が必要。

```bash
# パブリックサブネットに NAT Gateway を作成
aws ec2 create-nat-gateway \
  --subnet-id subnet-public-xxxxx \
  --allocation-id eipalloc-xxxxx

# プライベートサブネットのルートテーブルに NAT Gateway へのルートを追加
aws ec2 create-route \
  --route-table-id rtb-private-xxxxx \
  --destination-cidr-block 0.0.0.0/0 \
  --nat-gateway-id nat-xxxxx
```

## 1.3 セキュリティグループ

### ECS タスク用セキュリティグループ

```bash
aws ec2 create-security-group \
  --group-name ai-agent-ecs-task-sg \
  --description "Security group for AI Agent workspace ECS tasks" \
  --vpc-id vpc-xxxxx
```

#### インバウンドルール

Backend から ECS タスクへの通信のみ許可:

| ポート | プロトコル | ソース | 用途 |
|--------|-----------|--------|------|
| 9000 | TCP | Backend SG | workspace-agent HTTP API |
| 8081 | TCP | Backend SG | proxy-sidecar Admin HTTP |

```bash
SG_TASK=sg-ecs-task-xxxxx
SG_BACKEND=sg-backend-xxxxx

# workspace-agent (port 9000)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_TASK \
  --protocol tcp --port 9000 \
  --source-group $SG_BACKEND

# proxy-sidecar admin (port 8081)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_TASK \
  --protocol tcp --port 8081 \
  --source-group $SG_BACKEND
```

#### アウトバウンドルール

| ポート | プロトコル | 宛先 | 用途 |
|--------|-----------|------|------|
| 443 | TCP | 0.0.0.0/0 | Bedrock API、PyPI、npm 等 |
| 6379 | TCP | ElastiCache SG | Redis（Backend 経由ではなくタスクから直接接続が必要な場合のみ） |

```bash
# HTTPS（Bedrock API 等）
aws ec2 authorize-security-group-egress \
  --group-id $SG_TASK \
  --protocol tcp --port 443 \
  --cidr 0.0.0.0/0
```

**注意**: タスク内の 2 コンテナ間通信（workspace-agent ↔ proxy-sidecar）は `127.0.0.1` で行われるため、セキュリティグループの設定は不要。

### Backend 用セキュリティグループ

Backend が ECS タスクに接続するために、Backend の SG のアウトバウンドで ECS タスク SG への TCP 9000/8081 を許可する。

```bash
# Backend → ECS Task (port 9000)
aws ec2 authorize-security-group-egress \
  --group-id $SG_BACKEND \
  --protocol tcp --port 9000 \
  --source-group $SG_TASK

# Backend → ECS Task (port 8081)
aws ec2 authorize-security-group-egress \
  --group-id $SG_BACKEND \
  --protocol tcp --port 8081 \
  --source-group $SG_TASK
```

## 1.4 VPC エンドポイント（推奨）

プライベートサブネットから AWS API に NAT Gateway を経由せずアクセスするために、VPC エンドポイントの作成を推奨する。コスト削減とレイテンシ改善に有効。

### 必須（ECS Fargate の場合）

| エンドポイント | タイプ | 用途 |
|---------------|--------|------|
| `com.amazonaws.<region>.ecr.api` | Interface | ECR API（イメージプル時の認証） |
| `com.amazonaws.<region>.ecr.dkr` | Interface | ECR Docker Registry（イメージプル） |
| `com.amazonaws.<region>.s3` | Gateway | S3（ECR イメージレイヤー格納先 + ワークスペースファイル） |
| `com.amazonaws.<region>.logs` | Interface | CloudWatch Logs（コンテナログ送信） |

### 推奨

| エンドポイント | タイプ | 用途 |
|---------------|--------|------|
| `com.amazonaws.<region>.bedrock-runtime` | Interface | Bedrock API（AI モデル呼び出し） |
| `com.amazonaws.<region>.ecs` | Interface | ECS API（Backend からの RunTask 等） |

```bash
REGION=ap-northeast-1
VPC_ID=vpc-xxxxx
SUBNET_IDS=subnet-a,subnet-c
SG_VPCE=sg-vpce-xxxxx

# ECR API
aws ec2 create-vpc-endpoint \
  --vpc-id $VPC_ID \
  --service-name com.amazonaws.$REGION.ecr.api \
  --vpc-endpoint-type Interface \
  --subnet-ids $SUBNET_IDS \
  --security-group-ids $SG_VPCE \
  --private-dns-enabled

# S3 (Gateway型: 無料)
aws ec2 create-vpc-endpoint \
  --vpc-id $VPC_ID \
  --service-name com.amazonaws.$REGION.s3 \
  --vpc-endpoint-type Gateway \
  --route-table-ids rtb-private-xxxxx
```

## 1.5 ネットワーク設計のまとめ

```
┌─────────────────────────────────────────────────────────┐
│ VPC (10.0.0.0/16)                                       │
│                                                         │
│  ┌──────────────────────┐  ┌──────────────────────┐    │
│  │ Public Subnet (AZ-a) │  │ Public Subnet (AZ-c) │    │
│  │  - NAT Gateway       │  │  - (standby NAT GW)  │    │
│  │  - ALB (Backend)     │  │  - ALB (Backend)     │    │
│  └──────────┬───────────┘  └──────────┬───────────┘    │
│             │                         │                 │
│  ┌──────────▼───────────┐  ┌──────────▼───────────┐    │
│  │Private Subnet (AZ-a) │  │Private Subnet (AZ-c) │    │
│  │  - Backend (ECS/EC2) │  │  - Backend (ECS/EC2) │    │
│  │  - Workspace Tasks   │  │  - Workspace Tasks   │    │
│  │  - ElastiCache       │  │  - ElastiCache       │    │
│  │  - RDS               │  │  - RDS               │    │
│  └──────────────────────┘  └──────────────────────┘    │
│                                                         │
│  VPC Endpoints: ECR, S3, CloudWatch Logs, Bedrock, ECS  │
└─────────────────────────────────────────────────────────┘
```
