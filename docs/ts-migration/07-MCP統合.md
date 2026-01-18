# MCP統合設計

## 概要

本システムはModel Context Protocol（MCP）を使用して、外部システムやAPIとの連携を実現する。5種類のサーバータイプをサポートし、テナントごとに独自のMCPサーバーを設定できる。

---

## MCPサーバータイプ

### http

HTTP経由でMCPプロトコルを使用するサーバー。リモートのMCPサーバーに接続する最も一般的なタイプ。

**必要な設定**

- url：MCPサーバーのエンドポイントURL
- headers_template：認証ヘッダーなど（オプション）
- allowed_tools：許可するツール名リスト（オプション）

**使用例**

ServiceNow、Jira、Salesforceなどの外部SaaSとの連携。

### sse

Server-Sent Events経由でMCPプロトコルを使用するサーバー。リアルタイム通信が必要な場合に使用。

**必要な設定**

- url：SSEエンドポイントURL
- headers_template：認証ヘッダーなど（オプション）
- allowed_tools：許可するツール名リスト（オプション）

**使用例**

プッシュ通知を受け取るサービスとの連携。

### stdio

標準入出力を使用するローカルプロセスとして実行するサーバー。npxなどで起動するMCPサーバーに使用。

**必要な設定**

- command：実行コマンド（例：npx）
- args：コマンド引数の配列（例：["-y", "@anthropic-ai/mcp-server-filesystem", "/workspace"]）
- env：環境変数（オプション）
- allowed_tools：許可するツール名リスト（オプション）

**使用例**

ファイルシステムアクセス、ローカルデータベース操作など。

### builtin

アプリケーション内蔵のツール。外部プロセスやネットワーク通信なしで実行される。

**必要な設定**

- tools：ツール定義の配列。各ツールはname、description、input_schemaを持つ。

**ツール定義の構成**

- name：ツール名
- description：ツールの説明
- input_schema：入力パラメータのJSON Schema（type、properties、requiredを含む）

**使用例**

計算、現在時刻取得などのユーティリティ機能。

### openapi

OpenAPI仕様から自動生成されるツール。REST APIをMCPツールとして公開できる。

**必要な設定**

- openapi_spec：OpenAPI 3.0/3.1仕様（JSON形式）
- openapi_base_url：APIの基底URL
- headers_template：認証ヘッダーなど（オプション）
- allowed_tools：許可するツール名リスト（オプション）

**使用例**

既存のREST APIをAIエージェントから利用可能にする。

---

## 認証設定

### トークン置換機能

headers_templateフィールドでトークンプレースホルダを使用できる。「${token_name}」形式で記述すると、実行時にリクエストのtokensフィールドから対応する値に置換される。

**設定例**

headers_templateに以下を設定：
- Authorization: Bearer ${servicenow}
- X-API-Key: ${api_key}

**実行時**

リクエストのtokensに以下を指定：
- servicenow: "eyJhbGc..."
- api_key: "sk-1234..."

**結果**

実際のHTTPヘッダーに以下が設定される：
- Authorization: Bearer eyJhbGc...
- X-API-Key: sk-1234...

### トークンの受け渡し

クライアントはexecuteリクエストのtokensフィールドでMCPサーバー用のトークンを渡す。tokensはサーバー名をキー、トークン文字列を値とするオブジェクト形式。

---

## OpenAPI変換

### 変換プロセス

OpenApiMcpServiceがOpenAPI仕様を解析し、以下の変換を行う。

1. 各パス・メソッドの組み合わせに対してツールを生成
2. operationIdまたはパス+メソッドからツール名を生成
3. パスパラメータ、クエリパラメータ、リクエストボディを統合したinput_schemaを生成
4. summaryとdescriptionからツールの説明を生成
5. セキュリティスキームを解析して認証設定に反映

### ツール名の生成

operationIdが定義されている場合はそれを使用。未定義の場合は「メソッド_パス」形式で生成（例：get_users_list、post_incidents）。

### パラメータの統合

パスパラメータ、クエリパラメータ、リクエストボディのプロパティを単一のinput_schemaに統合する。requiredフィールドはそれぞれの必須設定を反映。

---

## SDK設定への変換

### McpServerServiceの役割

McpServerServiceのbuild_sdk_configメソッドが、データベースのMcpServerエンティティをSDK形式の設定に変換する。

### 変換処理

1. テナントのアクティブなMCPサーバーをすべて取得
2. 各サーバーのタイプに応じた設定を構築
3. トークン置換を実行
4. サーバー名をキーとした辞書形式で返却

### 結果の構造

SDKに渡されるMCPサーバー設定は以下の構造を持つ。

- キー：サーバー名
- 値：タイプ固有の設定（url、command、args、env、headers、toolsなど）

---

## ツール名の命名規則

MCPサーバーから提供されるツールは、SDKによって「mcp__{server_name}__{tool_name}」形式で命名される。

**例**

- サーバー名：servicenow
- ツール名：get_incidents
- 結果：mcp__servicenow__get_incidents

この命名規則により、同名のツールが複数のサーバーに存在しても衝突しない。

---

## エラーハンドリング

### MCPサーバー接続エラー

MCPサーバーへの接続に失敗した場合、ツール実行結果としてエラーが返される。エージェントはこのエラーを認識し、ユーザーに適切に伝達する。

### タイムアウト

MCPサーバーへのリクエストにはタイムアウトが設定される。タイムアウト時間はサーバータイプと設定に依存する。

### 認証エラー

トークンが無効または期限切れの場合、MCPサーバーは401/403エラーを返す。このエラーはツール実行結果としてエージェントに伝達される。

---

## セキュリティ考慮事項

### トークンの取り扱い

MCPトークンはリクエストごとに渡され、サーバーサイドには永続化されない。ログ出力時はサニタイズされ、「[REDACTED]」に置換される。

### allowed_toolsによる制限

allowed_toolsを設定することで、MCPサーバーから利用可能なツールを制限できる。設定しない場合はすべてのツールが利用可能。

### OpenAPI仕様の検証

openapi_specは信頼できるソースからのみ受け入れるべき。不正な仕様が設定された場合、予期しない動作やセキュリティリスクが生じる可能性がある。
