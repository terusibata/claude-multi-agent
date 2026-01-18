# API仕様

## 概要

本システムはRESTfulなAPIを提供する。ベースURLは`/api`であり、すべてのエンドポイントはテナントIDをパスに含む。レスポンスはJSON形式、ストリーミング実行のみServer-Sent Events形式となる。

---

## エンドポイント一覧

### 基本エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /health | ヘルスチェック |
| GET | / | ルート情報 |

### テナント管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /api/tenants | テナント一覧取得 |
| POST | /api/tenants | テナント作成 |
| GET | /api/tenants/{tenant_id} | テナント詳細取得 |
| PUT | /api/tenants/{tenant_id} | テナント更新 |
| DELETE | /api/tenants/{tenant_id} | テナント削除 |

### モデル管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /api/models | モデル一覧取得 |
| POST | /api/models | モデル作成 |
| GET | /api/models/{model_id} | モデル詳細取得 |
| PUT | /api/models/{model_id} | モデル更新 |
| DELETE | /api/models/{model_id} | モデル削除 |

### 会話管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /api/tenants/{tenant_id}/conversations | 会話一覧取得 |
| POST | /api/tenants/{tenant_id}/conversations | 会話作成 |
| GET | /api/tenants/{tenant_id}/conversations/{conversation_id} | 会話詳細取得 |
| PUT | /api/tenants/{tenant_id}/conversations/{conversation_id} | 会話更新 |
| POST | /api/tenants/{tenant_id}/conversations/{conversation_id}/execute | エージェント実行（SSE） |
| POST | /api/tenants/{tenant_id}/conversations/{conversation_id}/archive | 会話アーカイブ |
| GET | /api/tenants/{tenant_id}/conversations/{conversation_id}/messages | メッセージログ取得 |

### Agent Skills管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /api/tenants/{tenant_id}/skills | スキル一覧取得 |
| POST | /api/tenants/{tenant_id}/skills | スキル作成 |
| GET | /api/tenants/{tenant_id}/skills/{skill_id} | スキル詳細取得 |
| PUT | /api/tenants/{tenant_id}/skills/{skill_id} | スキル更新 |
| DELETE | /api/tenants/{tenant_id}/skills/{skill_id} | スキル削除 |
| GET | /api/tenants/{tenant_id}/skills/slash-commands | スラッシュコマンド一覧 |

### MCPサーバー管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /api/tenants/{tenant_id}/mcp-servers | MCPサーバー一覧取得 |
| POST | /api/tenants/{tenant_id}/mcp-servers | MCPサーバー作成 |
| GET | /api/tenants/{tenant_id}/mcp-servers/{mcp_server_id} | MCPサーバー詳細取得 |
| PUT | /api/tenants/{tenant_id}/mcp-servers/{mcp_server_id} | MCPサーバー更新 |
| DELETE | /api/tenants/{tenant_id}/mcp-servers/{mcp_server_id} | MCPサーバー削除 |

### 使用状況

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /api/tenants/{tenant_id}/usage | 使用状況一覧取得 |
| GET | /api/tenants/{tenant_id}/usage/summary | 使用状況サマリー |
| GET | /api/tenants/{tenant_id}/usage/cost-report | コストレポート |

### ワークスペース

| メソッド | パス | 説明 |
|---------|------|------|
| GET | /api/tenants/{tenant_id}/workspace/{conversation_id} | ワークスペース情報取得 |
| POST | /api/tenants/{tenant_id}/workspace/{conversation_id}/files | ファイルアップロード |
| GET | /api/tenants/{tenant_id}/workspace/{conversation_id}/files/{file_id} | ファイルダウンロード |
| DELETE | /api/tenants/{tenant_id}/workspace/{conversation_id}/files/{file_id} | ファイル削除 |

---

## 主要エンドポイント詳細

### テナント作成

**リクエスト**

tenant_idは必須。外部システムから割り当てられた識別子を指定する。system_promptとmodel_idはオプション。

**レスポンス**

作成されたテナント情報を返す。statusは「active」で初期化される。

### 会話作成

**リクエスト**

user_idは必須。model_idを省略するとテナントのデフォルトモデルが使用される。workspace_enabledのデフォルトはtrue。

**レスポンス**

作成された会話情報を返す。conversation_idはUUIDで自動生成される。session_idは初回実行後に設定される。

### エージェント実行（コアAPI）

**リクエスト**

user_inputは必須で、ユーザーからの入力メッセージを指定する。executorは実行者情報のオブジェクトで、user_id、name、emailが必須。tokensはMCPサーバー認証用のトークンマップで、サーバー名をキー、トークン文字列を値とする。preferred_skillsは優先使用するスキル名の配列。

**executor（実行者情報）の構成**

- user_id：ユーザー識別子
- name：ユーザー名
- email：メールアドレス
- employee_id：社員ID（オプション）

**レスポンス**

Server-Sent Events形式でストリーミング返却される。各イベントはイベント名とデータで構成される。

**SSEイベント種別**

- **session_start**：セッション開始。session_idとconversation_idを含む。
- **text_delta**：テキスト出力の断片。contentにテキストを含む。
- **tool_use**：ツール呼び出し開始。tool_use_id、tool_name、tool_inputを含む。
- **tool_result**：ツール実行結果。tool_use_id、result、is_errorを含む。
- **thinking**：拡張思考（Extended Thinking）。contentに思考内容を含む。
- **result**：実行完了。subtype（success/error_during_execution）、result、usage、cost_usd、num_turns、duration_msを含む。
- **error**：エラー発生。codeとmessageを含む。
- **heartbeat**：キープアライブ。空のデータ。

**usageの構成**

- input_tokens：入力トークン数
- output_tokens：出力トークン数
- cache_creation_5m_tokens：5分キャッシュ作成トークン数
- cache_creation_1h_tokens：1時間キャッシュ作成トークン数
- cache_read_tokens：キャッシュ読み込みトークン数
- total_tokens：合計トークン数

### スキル作成

**リクエスト**

nameは必須で、英数字・アンダースコア・ハイフンのみ許可。contentは必須で、SKILL.mdファイルの内容を指定する。slash_commandはUIでのスラッシュコマンド名（例：/review）。is_user_selectableはpreferred_skillsで指定可能かどうか。

**レスポンス**

作成されたスキル情報を返す。file_pathには実際のファイルパスが設定される。versionは1で初期化される。

### MCPサーバー作成

**リクエスト**

nameとtypeは必須。typeに応じて必要なフィールドが異なる。

- **http/sse**：urlが必須。headers_templateでトークン置換を設定可能。
- **stdio**：commandが必須。argsとenvはオプション。
- **builtin**：toolsが必須。ツール定義の配列を指定。
- **openapi**：openapi_specが必須。OpenAPI仕様のJSONを指定。openapi_base_urlで基底URLを指定。

**headers_templateのトークン置換**

「${token_name}」形式のプレースホルダを記述すると、実行時にリクエストのtokensフィールドから対応する値に置換される。

例：「Authorization」に「Bearer ${servicenow}」を設定すると、tokensに「servicenow」キーで渡されたトークンが埋め込まれる。

### 使用状況サマリー

**クエリパラメータ**

- from_date：集計開始日（ISO 8601形式）
- to_date：集計終了日（ISO 8601形式）
- group_by：集計単位。「day」「week」「month」のいずれか

**レスポンス**

指定期間・単位で集計された使用状況を返す。各期間のトークン数、コスト、実行回数が含まれる。

### コストレポート

**クエリパラメータ**

- from_date：集計開始日（必須）
- to_date：集計終了日（必須）

**レスポンス**

指定期間のモデル別コストレポートを返す。tenant_id、from_date、to_date、total_cost_usd、total_tokens、total_executionsと、by_model配列（モデル別の詳細）を含む。

---

## エラーレスポンス

すべてのエラーはerrorオブジェクトにcodeとmessageを含む形式で返却される。

**エラーコード一覧**

| コード | HTTPステータス | 説明 |
|-------|---------------|------|
| NOT_FOUND | 404 | リソースが見つからない |
| VALIDATION_ERROR | 400 | リクエストバリデーションエラー |
| INACTIVE_RESOURCE | 400 | リソースが非アクティブ状態 |
| SECURITY_ERROR | 403 | セキュリティ違反 |
| PATH_TRAVERSAL | 403 | パストラバーサル検出 |
| CONVERSATION_LOCKED | 409 | 会話がロック中 |
| FILE_SIZE_EXCEEDED | 413 | ファイルサイズ超過 |
| SDK_ERROR | 500 | Agent SDKエラー |
| INTERNAL_ERROR | 500 | 内部エラー |

---

## ストリーミングタイムアウト

エージェント実行のストリーミングには以下のタイムアウト設定がある。

- **実行タイムアウト**：300秒。この時間内に完了しない場合はエラー。
- **ハートビート間隔**：10秒。クライアント接続維持のため定期送信。
- **最大連続タイムアウト**：3回。ハートビートが3回連続で失敗すると接続終了。

---

## ページネーション

リスト取得APIはページネーションをサポートする。

**クエリパラメータ**

- limit：取得件数。デフォルトは20（会話）または100（使用状況）。
- offset：スキップ件数。デフォルトは0。

**レスポンス構造**

items配列に結果、totalに総件数、limitとoffsetにリクエスト値を含む。
