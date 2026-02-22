# ECS モード AWS セットアップガイド

本システムを AWS ECS 上で運用するために必要な AWS 側の全設定をまとめたガイド。

## 前提条件

- AWS アカウントと適切な管理者権限
- AWS CLI v2 がインストール・設定済み
- Docker がインストール済み（イメージビルド用）

## セットアップ手順

以下の順序で設定を進めてください。

| 順序 | ドキュメント | 内容 |
|------|-------------|------|
| 1 | [VPC・ネットワーク](./01-networking.md) | VPC、サブネット、セキュリティグループ、VPC エンドポイント |
| 2 | [IAM ロール・ポリシー](./02-iam-roles.md) | タスク実行ロール、タスクロール、Backend 実行ロール |
| 3 | [ECR・コンテナイメージ](./03-ecr-images.md) | ECR リポジトリ作成、イメージビルド・プッシュ |
| 4 | [ECS クラスター・タスク定義](./04-ecs-task-definition.md) | クラスター作成、タスク定義 JSON、Capacity Provider |
| 5 | [周辺 AWS サービス](./05-supporting-services.md) | ElastiCache (Redis)、RDS (PostgreSQL)、S3、CloudWatch Logs |
| 6 | [Backend 環境変数](./06-backend-configuration.md) | Backend コンテナに設定する環境変数一覧 |

## アーキテクチャ概要

```
                    ┌──────────────────────────────────────────────────────┐
                    │                    AWS VPC                          │
                    │                                                    │
  Client ──────►   │  ┌─────────────┐     ┌────────────────────────┐    │
                    │  │  Backend     │     │  ECS Task (awsvpc)     │    │
                    │  │  (ECS/EC2)   │────►│                        │    │
                    │  │              │:9000│  ┌──────────────────┐  │    │
                    │  │  FastAPI     │     │  │ workspace-agent  │  │    │
                    │  │  + Redis     │:8081│  │ (port 9000)      │  │    │
                    │  │  + RDS       │────►│  └───────┬──────────┘  │    │
                    │  └─────────────┘     │          │ 127.0.0.1   │    │
                    │                      │  ┌───────▼──────────┐  │    │
                    │                      │  │ proxy-sidecar    │  │    │
                    │                      │  │ (port 8080/8081) │──┼──► Bedrock API
                    │                      │  └──────────────────┘  │    │
                    │                      └────────────────────────┘    │
                    │                                                    │
                    │  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
                    │  │ElastiCache│  │   RDS    │  │    S3    │        │
                    │  │ (Redis)   │  │(Postgres)│  │(Workspace)│       │
                    │  └──────────┘  └──────────┘  └──────────┘        │
                    └──────────────────────────────────────────────────────┘
```

### ECS タスク内部構成

1つの ECS タスクに 2 つのコンテナを配置（awsvpc モード）:

| コンテナ | ポート | essential | 役割 |
|----------|--------|-----------|------|
| `workspace-agent` | 9000 | true | AI エージェント実行 (Claude Agent SDK) |
| `proxy-sidecar` | 8080, 8081 | false | Credential Injection Proxy (SigV4 署名注入 + ドメインホワイトリスト) |

### 使用する AWS サービス一覧

| サービス | 用途 |
|----------|------|
| ECS (Fargate or EC2) | ワークスペースコンテナの実行 |
| ECR | コンテナイメージの格納 |
| VPC / Subnet / SG | ネットワーク隔離 |
| VPC Endpoints | プライベートサブネットからの AWS API アクセス |
| IAM | タスクロール、実行ロール、Backend 権限 |
| ElastiCache (Redis) | コンテナ状態管理、WarmPool、分散ロック |
| RDS (PostgreSQL) | 会話・テナント・使用量データ |
| S3 | ワークスペースファイル永続化、Skills バックアップ |
| CloudWatch Logs | コンテナログ収集・デバッグ |
| Bedrock | Claude モデル API アクセス |
