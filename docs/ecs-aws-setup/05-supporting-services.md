# 5. 周辺 AWS サービス

## 5.1 ElastiCache (Redis)

### 用途

| 機能 | Redis キー例 | 説明 |
|------|-------------|------|
| コンテナ状態管理 | `workspace:container:{conv_id}` | コンテナメタデータ (Hash, TTL 3600s) |
| 逆引きマッピング | `workspace:container_reverse:{id}` | container_id → conversation_id (TTL 3600s) |
| ECS タスクマッピング | `workspace:ecs_task:{id}` | container_id → task_arn (TTL 3600s) |
| WarmPool キュー | `workspace:warm_pool` | 待機コンテナの ID リスト (List) |
| WarmPool 情報 | `workspace:warm_pool_info:{id}` | WarmPool コンテナ情報 (Hash, TTL 1800s) |
| WarmPool 設定 | `workspace:warm_pool:config` | 動的設定変更用 (Hash) |
| 分散ロック | `lock:execute:{conv_id}` | 同一会話の並行実行防止 (TTL 900s) |
| レート制限 | `rate_limit:*` | API レート制限 |
| セッション | `session:*` | API セッション管理 |

### クラスター作成

```bash
aws elasticache create-replication-group \
  --replication-group-id ai-agent-redis \
  --replication-group-description "AI Agent Redis cluster" \
  --engine redis \
  --engine-version 7.1 \
  --cache-node-type cache.r7g.large \
  --num-cache-clusters 2 \
  --automatic-failover-enabled \
  --multi-az-enabled \
  --at-rest-encryption-enabled \
  --transit-encryption-enabled \
  --auth-token "<STRONG_PASSWORD>" \
  --cache-subnet-group-name ai-agent-redis-subnet \
  --security-group-ids sg-redis-xxxxx \
  --snapshot-retention-limit 7
```

### パラメータグループ（推奨設定）

```bash
aws elasticache create-cache-parameter-group \
  --cache-parameter-group-name ai-agent-redis-params \
  --cache-parameter-group-family redis7 \
  --description "AI Agent Redis parameters"

aws elasticache modify-cache-parameter-group \
  --cache-parameter-group-name ai-agent-redis-params \
  --parameter-name-values \
    "ParameterName=maxmemory-policy,ParameterValue=allkeys-lru" \
    "ParameterName=timeout,ParameterValue=300" \
    "ParameterName=tcp-keepalive,ParameterValue=60"
```

### セキュリティグループ

```bash
# Redis SG: Backend/Backend ECS タスクからの 6379 のみ許可
aws ec2 authorize-security-group-ingress \
  --group-id sg-redis-xxxxx \
  --protocol tcp --port 6379 \
  --source-group sg-backend-xxxxx
```

### サイジング目安

| 同時コンテナ数 | ノードタイプ | メモリ | 備考 |
|--------------|------------|--------|------|
| ~50 | cache.t4g.medium | 3.09 GB | 検証・小規模 |
| 50-200 | cache.r7g.large | 13.07 GB | 標準 |
| 200+ | cache.r7g.xlarge | 26.32 GB | 大規模 |

## 5.2 RDS (PostgreSQL)

### 用途

- 会話データ（メッセージ履歴、メタデータ）
- テナント管理
- モデル定義
- 使用量記録（トークン数、コスト）
- Skills 定義

### インスタンス作成

```bash
aws rds create-db-instance \
  --db-instance-identifier ai-agent-db \
  --db-instance-class db.r6g.large \
  --engine postgres \
  --engine-version 16.4 \
  --master-username aiagent \
  --master-user-password "<STRONG_PASSWORD>" \
  --allocated-storage 100 \
  --max-allocated-storage 500 \
  --storage-type gp3 \
  --storage-encrypted \
  --multi-az \
  --db-subnet-group-name ai-agent-db-subnet \
  --vpc-security-group-ids sg-rds-xxxxx \
  --backup-retention-period 7 \
  --deletion-protection \
  --publicly-accessible false
```

### セキュリティグループ

```bash
# RDS SG: Backend からの 5432 のみ許可
aws ec2 authorize-security-group-ingress \
  --group-id sg-rds-xxxxx \
  --protocol tcp --port 5432 \
  --source-group sg-backend-xxxxx
```

### 接続 URL 形式

```
DATABASE_URL=postgresql+asyncpg://<USER>:<PASSWORD>@<RDS_ENDPOINT>:5432/<DB_NAME>
```

## 5.3 S3

### 用途

| プレフィックス | 用途 | 説明 |
|--------------|------|------|
| `workspaces/{tenant_id}/{conversation_id}/` | ワークスペースファイル | コンテナ↔S3 間でファイル同期 |
| `workspaces/{tenant_id}/{conversation_id}/_sdk_session/` | SDK セッションファイル | 会話の継続性を維持 |
| `skills/{tenant_id}/` | Skills バックアップ | カスタムスキルの永続化 |

### バケット作成

```bash
BUCKET_NAME=ai-agent-workspaces-<ACCOUNT_ID>

aws s3api create-bucket \
  --bucket $BUCKET_NAME \
  --region ap-northeast-1 \
  --create-bucket-configuration LocationConstraint=ap-northeast-1

# バージョニング有効化
aws s3api put-bucket-versioning \
  --bucket $BUCKET_NAME \
  --versioning-configuration Status=Enabled

# 暗号化設定（SSE-S3）
aws s3api put-bucket-encryption \
  --bucket $BUCKET_NAME \
  --server-side-encryption-configuration '{
    "Rules": [
      {
        "ApplyServerSideEncryptionByDefault": {
          "SSEAlgorithm": "AES256"
        }
      }
    ]
  }'

# パブリックアクセスブロック
aws s3api put-public-access-block \
  --bucket $BUCKET_NAME \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### ライフサイクルポリシー

`deployment/s3/lifecycle-policy.json` を適用する:

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket $BUCKET_NAME \
  --lifecycle-configuration file://deployment/s3/lifecycle-policy.json
```

ポリシー内容:

| ルール | 条件 | アクション |
|--------|------|----------|
| 旧バージョンクリーンアップ | 非現行バージョン 30 日経過 | 削除 |
| Glacier 移行 | 90 日経過 | GLACIER へ移行 |
| Glacier 期限切れ | 270 日経過 | 削除 |

### CORS 設定（フロントエンドから直接アクセスする場合のみ）

通常は Backend 経由でアクセスするため不要。

## 5.4 CloudWatch Logs

### 用途

ECS タスクのコンテナログを収集する。Backend がデバッグ時に `GetLogEvents` API で読み取る。

### ロググループ作成

```bash
aws logs create-log-group \
  --log-group-name /ecs/workspace-agent \
  --retention-in-days 14

# タグ付け
aws logs tag-log-group \
  --log-group-name /ecs/workspace-agent \
  --tags Application=ai-agent,Component=workspace
```

### ログストリームの命名規則

タスク定義の `awslogs-stream-prefix` で設定:

```
/ecs/workspace-agent/{prefix}/{container-name}/{task-id}
```

例:
- `ecs/workspace-agent/abcdef1234567890` — workspace-agent のログ
- `proxy/proxy-sidecar/abcdef1234567890` — proxy-sidecar のログ

Backend は `get_container_logs()` でこのログを取得する。ログストリーム名はタスク ARN から以下のように構築:

```
{stream_prefix}/{container_name}/{task_id}
```

`task_id` は `task_arn.split("/")[-1]` で取得。

### 保持期間の選定

| 環境 | 推奨保持期間 |
|------|------------|
| 開発 | 7 日 |
| ステージング | 14 日 |
| 本番 | 30 日 |

## 5.5 Bedrock

### モデルアクセスの有効化

使用するリージョンで Bedrock モデルアクセスをリクエストする:

```
AWS Console → Bedrock → Model access → Manage model access
```

本システムが使用するモデル:

| 設定名 | デフォルトモデル ID | 用途 |
|--------|------------------|------|
| `ANTHROPIC_SONNET_MODEL` | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | メインモデル |
| `ANTHROPIC_HAIKU_MODEL` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | 軽量サブエージェント |

**Cross-Region Inference** (`global.` プレフィックス): リージョン間で自動ルーティングされる。個別リージョン指定も可能（例: `anthropic.claude-sonnet-4-5-20250929-v1:0`）。

### Bedrock エンドポイント

proxy-sidecar がアクセスする Bedrock エンドポイント:

```
https://bedrock-runtime.<REGION>.amazonaws.com
```

ドメインホワイトリストに使用リージョンの `bedrock-runtime.*.amazonaws.com` を追加すること。
