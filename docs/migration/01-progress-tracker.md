# Workspace Container Isolation - 移行進捗管理

**最終更新**: 2026-02-07
**全体進捗**: Step 0/10 完了

---

## 進捗サマリー

| Step | タスク | ステータス | 備考 |
|------|--------|-----------|------|
| 1 | 基盤準備 | :black_square_button: 未着手 | 依存パッケージ・設定・モデル |
| 2 | ベースイメージ | :black_square_button: 未着手 | Dockerfile + requirements |
| 3 | workspace_agent | :black_square_button: 未着手 | コンテナ内FastAPI |
| 4 | Credential Injection Proxy | :black_square_button: 未着手 | ドメインWL + SigV4 |
| 5 | Container Orchestrator | :black_square_button: 未着手 | aiodocker + Redis |
| 6 | ファイル同期 | :black_square_button: 未着手 | S3 <-> Container |
| 7 | 実行エンジン書き換え | :black_square_button: 未着手 | ExecuteService全面改修 |
| 8 | アプリケーション統合 | :black_square_button: 未着手 | main.py + docker-compose |
| 9 | セキュリティ検証 | :black_square_button: 未着手 | 認証情報除去・多層防御確認 |
| 10 | クリーンアップ | :black_square_button: 未着手 | 旧コード削除・ドキュメント |

---

## 詳細進捗

### Step 1: 基盤準備

- [ ] 1.1 依存パッケージ追加（aiodocker等）→ `requirements.txt`
- [ ] 1.2 コンテナ関連設定の追加 → `app/config.py`
- [ ] 1.3 ContainerInfo等のデータモデル定義 → `app/services/container/models.py`
- [ ] 1.4 コンテナ作成設定の定義 → `app/services/container/config.py`

### Step 2: ワークスペースベースイメージ

- [ ] 2.1 ベースイメージDockerfile作成 → `workspace-base/Dockerfile`
- [ ] 2.2 コンテナ内Python依存パッケージ定義 → `workspace-base/workspace-requirements.txt`

### Step 3: コンテナ内エージェント（workspace_agent）

- [ ] 3.1 リクエスト/レスポンスモデル定義 → `workspace_agent/models.py`
- [ ] 3.2 Claude SDKクライアントラッパー → `workspace_agent/sdk_client.py`
- [ ] 3.3 FastAPI メインアプリ（UDS対応） → `workspace_agent/main.py`

### Step 4: Credential Injection Proxy

- [ ] 4.1 ドメインホワイトリスト → `app/services/proxy/domain_whitelist.py`
- [ ] 4.2 AWS SigV4署名ユーティリティ → `app/services/proxy/sigv4.py`
- [ ] 4.3 CredentialInjectionProxy本体 → `app/services/proxy/credential_proxy.py`

### Step 5: Container Orchestrator

- [ ] 5.1 コンテナライフサイクル管理 → `app/services/container/lifecycle.py`
- [ ] 5.2 ContainerOrchestrator本体 → `app/services/container/orchestrator.py`
- [ ] 5.3 WarmPoolManager → `app/services/container/warm_pool.py`
- [ ] 5.4 GC（ガベージコレクター）→ `app/services/container/gc.py`

### Step 6: ファイル同期

- [ ] 6.1 Container ↔ S3ファイル同期 → `app/services/workspace/file_sync.py`
- [ ] 6.2 WorkspaceService改修 → `app/services/workspace_service.py`

### Step 7: 実行エンジン書き換え

- [ ] 7.1 ExecuteService全面書き換え → `app/services/execute_service.py`
- [ ] 7.2 ストリーミングAPI改修 → `app/api/conversations.py`

### Step 8: アプリケーション統合

- [ ] 8.1 main.pyにOrchestrator/GC統合 → `app/main.py`
- [ ] 8.2 ヘルスチェックにコンテナ状態追加 → `app/api/health.py`
- [ ] 8.3 docker-compose.yml更新 → `docker-compose.yml`
- [ ] 8.4 ホストDockerfile更新 → `Dockerfile`

### Step 9: セキュリティ検証

- [ ] 9.1 AWS認証情報の環境変数からの除去 → `app/main.py`
- [ ] 9.2 セキュリティレイヤー確認 → ドキュメント

### Step 10: クリーンアップ

- [ ] 10.1 不要コード削除 → 旧execute関連
- [ ] 10.2 ドキュメント更新 → `docs/`

---

## 変更履歴

| 日時 | 内容 |
|------|------|
| 2026-02-07 | 移行計画書・進捗管理ドキュメント作成 |
