# Amazon Bedrock AgentCore 移行評価レポート

## Context

本リポジトリ（Claude Agent SDK + AWS ECS/Docker マルチテナントAIエージェント実行基盤）を
Amazon Bedrock AgentCoreに移行した場合の技術評価。ゼロベースでの再実装を前提とする。

---

## 1. ローカル開発

### 結論: ローカル開発は問題なく可能

AgentCoreはローカル開発を正式サポートしており、AWSにデプロイしなくてもテスト可能。

### 開発ツール

| ツール | 用途 |
|---|---|
| `agentcore dev` | ローカル開発サーバー（ホットリロード対応、localhost:8080） |
| `agentcore invoke --dev "prompt"` | ローカルサーバーへのテスト実行 |
| `agentcore launch -l` | Docker(ARM64)でのローカルコンテナ実行 |
| `agentcore deploy` | クラウドデプロイ |
| `agentcore create` | プロジェクトスキャフォールド |

CLIは `pip install bedrock-agentcore-starter-toolkit`（v0.3.0、experimental）でインストール。

### ローカル開発ワークフロー

```
# プロジェクト作成
agentcore create

# ローカル開発サーバー起動（ホットリロード対応）
agentcore dev

# 別ターミナルでテスト実行
agentcore invoke --dev "Hello!"

# コード変更 → 自動リロード → 再テスト（デプロイ不要）
```

### フレームワークのローカル独立性

AgentCoreはフレームワーク非依存。以下のフレームワークはいずれも単独でローカル実行可能：

| フレームワーク | ローカルモデル対応 |
|---|---|
| Strands Agents | Ollama, LiteLLM |
| LangGraph | LangChain対応の任意プロバイダ |
| CrewAI | 複数LLMプロバイダ |
| Claude Agent SDK | Bedrock API（プロキシなしで直接呼出） |
| カスタムFastAPI | 任意 |

### 注意点

- AgentCoreのマネージドサービス（Memory, Gateway, Identity）はクラウド専用 → ローカルではモック or スキップが必要
- AWS資格情報はBedrock利用時のみ必要（APIキーベースの他プロバイダでは不要）
- `bedrock-agentcore` SDK（v1.3.1）自体はローカルで動作するASGIラッパー
- 現在のDocker Composeベースの開発体験とは異なるが、同等以上の開発ループを実現可能

---

## 2. コスト比較

### 前提条件

- 100同時セッション、10,000セッション/日（月300,000セッション）
- 平均セッション時間: 5分
- アクティブCPU時間: セッション時間の30%（残り70%はLLM応答待ちI/O）
- メモリ: 平均1.5GB/セッション
- 1 vCPU/セッション
- Claude Sonnet 4 via Bedrock: 5ターン/セッション、各ターン2,000入力+1,000出力トークン

### AgentCore コスト（月額）

| コンポーネント | 単価 | 月額コスト |
|---|---|---|
| **Runtime CPU** | $0.0895/vCPU-hour（I/O待ち非課金） | $671 |
| **Runtime Memory** | $0.00945/GB-hour | $355 |
| **Gateway** | $0.005/1,000呼出 | $8 |
| **Memory** | $0.25/1,000イベント + $0.50/1,000検索 | $450 |
| **Identity** | Runtime経由で無料 | $0 |
| **Observability** | CloudWatch連携 | ~$1 |
| **AgentCoreインフラ合計** | | **~$1,485** |
| Claude Sonnet 4 (LLM推論) | | $31,500 |
| **総合計** | | **~$32,985** |

RuntimeのI/O待ち非課金がキーポイント。エージェントワークロードの70%はLLM応答待ちであり、その間CPU課金がゼロ。

### 現行構成 セルフホスト コスト（月額）

| コンポーネント | 月額コスト（ピーク時プロビジョニング） | 月額コスト（適正化後） |
|---|---|---|
| **ECS Fargate** (100タスク常時) | $3,555 | $1,777 |
| **ElastiCache Redis** (HA) | $302 | $302 |
| **RDS PostgreSQL** (Multi-AZ) | $392 | $392 |
| **S3** | $11 | $11 |
| **ALB** | $45 | $45 |
| **Prometheus + Grafana** | $38 | $38 |
| **インフラ合計** | **$4,343** | **$2,565** |
| Claude Sonnet 4 (LLM推論) | $31,500 | $31,500 |
| **総合計** | **$35,843** | **$34,066** |

### 比較サマリー

| | AgentCore | セルフホスト(ピーク) | セルフホスト(適正化) |
|---|---|---|---|
| インフラコスト | **$1,485** | $4,343 | $2,565 |
| LLM推論コスト | $31,500 | $31,500 | $31,500 |
| 合計 | **$32,985** | $35,843 | $34,066 |
| インフラ削減率 | — | **65.8%削減** | **42.1%削減** |

### コストに関する結論

- AgentCoreの方がインフラコストは40-66%安い（I/O待ち非課金が最大の要因）
- LLM推論が総コストの95%を占めるため、インフラの差は全体では小さい
- プロンプト最適化やキャッシュ利用（$0.30/M tokens vs $3.00/M）の方がコスト影響が大きい
- セルフホストの隠れコスト（運用工数、オンコール、セキュリティパッチ）は未計上

---

## 3. 実装時の懸念点と解決策

### 懸念点 1: Claude Agent SDK はAgentCore Runtimeで動作するか？

**解決済み: 動作確認済み**

- BGL社の本番事例: Claude Agent SDK + AgentCoreで業務用BIシステムを本番運用
- AWS公式サンプル: `sample-agentic-ai-with-claude-agent-sdk-and-amazon-bedrock-agentcore` (GitHub)
- builder.aws チュートリアル: "Deploying Claude Agent SDK on Amazon Bedrock AgentCore Runtime"
- SDKの切り替えやアダプタは不要

AgentCoreのサービスコントラクト: ポート8080で `/invocations` (POST) と `/ping` (GET) を公開するコンテナであれば動作。現在の `workspace_agent` の `/execute` エンドポイント（ポート9000/UDS）をこのコントラクトに変更するだけ。

### 懸念点 2: カスタム依存ライブラリ（pymupdf, openpyxl等）の扱い

**解決策: コンテナイメージにバンドル**

- コンテナベースデプロイでカスタム依存は全てDockerイメージに含められる
- 最大イメージサイズ: 2GB
- ARM64アーキテクチャが必須 → `FROM --platform=linux/arm64 python:3.11-slim`
- Node.js（Claude Agent SDK CLI用）もARM64ビルドが必要

### 懸念点 3: SigV4プロキシパターンの置き換え

**解決済み: 完全に不要になる**

AgentCore Runtimeは実行ロール（Execution Role）を通じてBedrock APIに直接アクセス。
`CredentialInjectionProxy` の812行のSigV4署名ロジックは全て不要。

### 懸念点 4: MCP認証ヘッダの動的注入

**解決策: AgentCore Identity Token Vault + Gateway Interceptors**

| 現行パターン | AgentCore パターン |
|---|---|
| `credential_proxy.py` がリクエスト毎にヘッダ注入 | Identity Token VaultにOAuth/APIキーを保存 |
| `${token}` テンプレートを実行時に解決 | `@requires_access_token` デコレータで宣言的に注入 |
| per-request動的トークン解決 | Gateway Interceptorsでテナント別アクセス制御 |

注意: 現在のper-request動的トークン解決（`request.tokens` からの解決）にはGateway Interceptorsのカスタム実装が必要。

### 懸念点 5: OpenAPI → MCP変換のスキーマ制限

**解決策: スキーマの事前解決**

AgentCore Gatewayの制限:
- `oneOf` 非サポート
- `$ref`, `$defs`, `$anchor` 非サポート
- スキーマは完全に解決済み（self-contained）である必要

→ Gateway登録前にスキーマを事前に完全解決する前処理ステップを追加。既存の解決ロジックを再利用可能。

### 懸念点 6: ファイル同期（S3 ↔ コンテナ）

**解決策: エージェント内部でのS3直接操作に移行**

AgentCoreには `docker exec` 相当のAPIがない:

| 現行 | AgentCore |
|---|---|
| ホストがdocker exec/tarでコンテナにファイルを転送 | エージェント自身がS3から直接ファイルを取得 |
| ホストがコンテナからファイルを回収 | エージェント自身がS3に結果をアップロード |

セッション開始時にinvocation payload（最大100MB）またはS3から取得し、終了時にS3にアップロード。

### 懸念点 7: ドメインホワイトリスト

**解決策: VPC + AWS Network Firewall**

AgentCoreにはビルトインのドメインホワイトリストがないため:
1. AgentCore RuntimeをVPC内で実行（VPC設定サポートあり）
2. AWS Network Firewallでドメインレベルのフィルタリング
3. Security Groupsで基本的なIP/ポート制御

### 懸念点 8: セッション状態の永続化

**解決策: AgentCore Memory + S3**

| 用途 | 解決策 |
|---|---|
| 短期メモリ（会話内） | AgentCore Memory（短期イベント） |
| 長期メモリ（会話跨ぎ） | AgentCore Memory（長期記憶） |
| ワークスペースファイル | S3（エージェント自身が操作） |
| 会話メタデータ | PostgreSQL（ホストアプリケーション側で管理継続） |

セッション内のファイルシステムはエフェメラル（セッション終了で消去）。

### 懸念点 9: SSEストリーミング

**解決済み: 完全サポート**

- `/invocations` エンドポイントで `Content-Type: text/event-stream` を返却可能
- 最大ストリーミング時間: 60分
- WebSocket双方向通信もサポート（`/ws` エンドポイント）

### 懸念点 10: マルチテナント

**解決策: セッション属性 + JWT + Gateway Interceptors**

AWS公式サンプルが複数パターンを提供:
- `sample-agentcore-multi-tenant`: JWT ベースのテナント分離
- `sample-bedrock-agentcore-multitenant`: Cognito グループベースのティア抽出
- `sample-multi-tenant-agent-core-app`: セッション属性による動的テナントルーティング

テナント管理DB（PostgreSQL）は引き続きホストアプリケーション側で管理が必要。

### 懸念点 11: サービスクォータ

| リソース | 制限値 | 調整可能 |
|---|---|---|
| アクティブセッション/アカウント | 1,000 (US) / 500 (他) | Yes |
| Dockerイメージサイズ | 2 GB | No |
| セッションあたりハードウェア | 2 vCPU / 8 GB | No |
| 同期リクエストタイムアウト | 15分 | No |
| ペイロードサイズ | 100 MB | No |
| SSEストリーミング最大時間 | 60分 | No |
| 非同期ジョブ最大時間 | 8時間 | Yes |
| アイドルセッションタイムアウト | 15分 | Yes |
| InvokeAgentRuntime TPS | 25/エンドポイント | Yes |

注意: 2 vCPU / 8 GB のハードウェア上限は調整不可。

### 懸念点 12: Preview機能への依存

コア機能（Runtime, Gateway, Memory, Identity, Code Interpreter, Browser, Observability）は全てGA。
Preview段階はPolicy（ドメイン制御）とEvaluations（品質評価）のみ。

---

## 4. 削除可能コード vs 残すコード

### 削除可能（AgentCoreが代替、約3,300行）

- `app/services/container/` (orchestrator, lifecycle, ecs_manager, warm_pool, gc)
- `app/services/proxy/` (credential_proxy, sigv4, domain_whitelist)
- `app/infrastructure/distributed_lock.py`
- `app/middleware/rate_limit.py`
- `app/infrastructure/metrics.py`

### 変更が必要

- `workspace_agent/main.py` → `/invocations` コントラクトへ
- `workspace_agent/sdk_client.py` → プロキシ依存除去
- `app/services/workspace/file_sync.py` → エージェント内S3操作
- `app/services/execute_service.py` → AgentCore InvokeAgentRuntime API
- `Dockerfile` → ARM64ビルド対応

### そのまま維持

- `app/api/` (全API層)
- `app/repositories/` (DB層)
- `app/services/skill_service.py`
- `app/services/prompt_builder.py`
- `workspace_agent/file_tools/`

---

## 5. 検証ステップ

### ステップ1: ローカルPoC
1. `agentcore create` でスキャフォールド
2. 既存の `workspace_agent` を `/invocations` コントラクトに適合
3. `agentcore dev` でローカル動作確認
4. Claude Agent SDKのBedrock直接呼出（プロキシなし）を検証

### ステップ2: クラウドデプロイ
1. ARM64 Dockerfileの作成と `agentcore deploy`
2. AgentCore Identity でOAuth設定
3. AgentCore Gateway でMCPツール登録
4. SSEストリーミングのE2E動作確認

### ステップ3: 負荷テスト
1. 同時セッション数の段階的増加（10 → 50 → 100）
2. コールドスタート時間の計測
3. コスト実測値とのクロスチェック

---

## Sources

- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)
- [AgentCore Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)
- [AgentCore Starter Toolkit](https://aws.github.io/bedrock-agentcore-starter-toolkit/)
- [BGL Case Study: Claude Agent SDK + AgentCore](https://aws.amazon.com/blogs/machine-learning/democratizing-business-intelligence-bgls-journey-with-claude-agent-sdk-and-amazon-bedrock-agentcore/)
- [AWS Sample: Claude Agent SDK + AgentCore](https://github.com/aws-samples/sample-agentic-ai-with-claude-agent-sdk-and-amazon-bedrock-agentcore)
- [AWS Sample: Multi-Tenant AgentCore](https://github.com/aws-samples/sample-agentcore-multi-tenant)
- [AgentCore Gateway Interceptors](https://aws.amazon.com/blogs/machine-learning/apply-fine-grained-access-control-with-bedrock-agentcore-gateway-interceptors/)
- [AgentCore Identity Blog](https://aws.amazon.com/blogs/machine-learning/introducing-amazon-bedrock-agentcore-identity-securing-agentic-ai-at-scale/)
- [AgentCore Service Quotas](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html)
