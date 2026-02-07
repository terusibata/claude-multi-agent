# ギャップ分析 - 現行 vs 移行後

**作成日**: 2026-02-07

---

## 1. 実行モデルの変更

### 現行: in-process実行

```
Client → Backend (FastAPI)
           └─ ExecuteService.execute_streaming()
               └─ Claude Agent SDK (in-process)
                   ├─ ツール実行（ホストFS直接アクセス）
                   └─ AWS認証（os.environ から取得）
```

**問題点**:
- `app/services/execute_service.py` がClaude Agent SDKを直接import・実行
- ツール実行がホストのファイルシステムに直接アクセス
- `app/main.py:102-108` で `os.environ` にAWS認証情報を設定 → 全セッション共有
- `app/services/execute/aws_config.py` がグローバル環境変数を参照

### 移行後: コンテナ隔離実行

```
Client → Backend (FastAPI)
           └─ ContainerOrchestrator.execute()
               ├─ コンテナ取得/作成 (WarmPool or 新規)
               ├─ S3 → コンテナへファイル同期
               ├─ Unix Socket → workspace_agent /execute
               │     └─ Claude Agent SDK (コンテナ内)
               │         ├─ ツール実行（/workspace内のみ）
               │         └─ 外部通信 → Proxy.sock → CredentialInjectionProxy
               ├─ SSEイベント中継
               └─ コンテナ → S3へファイル同期
```

---

## 2. ファイル別影響分析

### 2.1 `app/services/execute_service.py` (1,189行) → **全面書き換え**

**現行の責務** (削除対象):
- Claude Agent SDK の直接import・設定
- in-processでのエージェント実行
- ツール実行の直接ハンドリング
- メッセージストリームの直接処理

**移行後の責務**:
- ContainerOrchestrator経由でのコンテナ内実行指示
- Unix Socket経由のSSEストリーム中継
- コンテナ障害時のリカバリ（新コンテナ割当 + S3復元）
- トークン使用量・コスト計算（コンテナ内から返却されるメトリクス）

**影響を受ける依存関係**:
- `app/services/execute/options_builder.py` → コンテナへのリクエスト組み立てに変更
- `app/services/execute/message_processor.py` → コンテナからのSSEイベント解析に変更
- `app/services/execute/tool_tracker.py` → コンテナ内のツール実行追跡
- `app/services/execute/context.py` → コンテナ情報を追加
- `app/services/execute/aws_config.py` → **削除**（認証情報はProxy側に移動）

### 2.2 `app/main.py` (417行) → **大幅改修**

**変更点**:
- `lifespan()` にContainerOrchestrator初期化追加
- `lifespan()` にGCループ起動追加
- `lifespan()` のシャットダウンに全コンテナ破棄追加
- `os.environ` へのAWS認証情報設定を**完全削除**
- ヘルスチェックにコンテナシステム状態追加

### 2.3 `app/config.py` (242行) → **設定追加**

**追加設定**:
```
# コンテナ設定
container_image: str               # workspace-base:latest
container_cpu_limit: int           # 200000 (2 cores)
container_memory_limit: int        # 2GB
container_pids_limit: int          # 100
container_disk_limit: str          # 5G
container_inactive_ttl: int        # 3600s
container_absolute_ttl: int        # 28800s (8h)
container_execution_timeout: int   # 600s (10min)
container_grace_period: int        # 30s
container_healthcheck_interval: int # 30s

# WarmPool設定
warm_pool_min_size: int            # 2
warm_pool_max_size: int            # 10
warm_pool_ttl: int                 # 1800s (30min)

# Proxy設定
proxy_domain_whitelist: str        # カンマ区切り
proxy_log_all_requests: bool       # True

# Docker設定
docker_socket_path: str            # /var/run/docker.sock
workspace_socket_base_path: str    # /var/run/ws/
```

**削除候補の設定**:
- `workspace_temp_dir` → コンテナ内に移動、ホスト側不要

### 2.4 `app/api/conversations.py` → **ストリーミング中継の改修**

**現行**: `ExecuteService.execute_streaming()` が直接SSEイベントを生成
**移行後**: コンテナ内 `workspace_agent` が生成するSSEイベントをUnix Socket経由で中継

### 2.5 `docker-compose.yml` → **大幅更新**

**追加内容**:
- Docker Socketマウント: `/var/run/docker.sock:/var/run/docker.sock`
- Unix Socketベースディレクトリ: `/var/run/ws` volume
- ワークスペースコンテナ用ネットワーク設定なし（`--network none`はコンテナ個別）
- backend-networkブリッジネットワーク定義

### 2.6 `Dockerfile` → **更新**

**追加内容**:
- aiodocker等の新依存パッケージ
- Docker Socketアクセス用のグループ設定
- `/var/run/ws` ディレクトリ作成

---

## 3. 削除対象コード

| ファイル | 削除対象 | 理由 |
|---------|---------|------|
| `app/main.py` | `os.environ["AWS_ACCESS_KEY_ID"] = ...` 他 | Proxy側で管理 |
| `app/services/execute/aws_config.py` | ファイル全体 | Proxy側で管理 |
| `app/services/execute/options_builder.py` | SDK直接呼び出しコード | コンテナ側に移動 |
| `app/services/workspace_service.py` | ローカルファイルパス関連 | コンテナ内に変更 |

---

## 4. API互換性マトリクス

| エンドポイント | リクエスト形式 | レスポンス形式 | 変更 |
|---------------|-------------|--------------|------|
| `POST /conversations/{id}/stream` | `StreamRequest` (維持) | SSE (維持) | 内部実装のみ変更 |
| `POST /conversations/{id}/files/upload` | multipart (維持) | `ConversationFileInfo` (維持) | S3パスは維持 |
| `GET /conversations/{id}/files` | (維持) | `WorkspaceFileList` (維持) | 変更なし |
| `GET /conversations/{id}/files/{id}/download` | (維持) | Binary (維持) | 変更なし |
| `GET /health` | (維持) | JSON (拡張) | コンテナ情報追加 |

**結論**: フロントエンド側の変更は不要。内部実装のみの変更。
