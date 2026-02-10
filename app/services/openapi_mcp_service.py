"""
OpenAPI仕様からMCPサーバーを動的に生成するサービス

OpenAPI仕様を解析し、各エンドポイントをMCPツールに変換。
ツール実行時にHTTP APIを呼び出すプロキシとして機能。
"""
import json
import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx
import structlog

logger = structlog.get_logger(__name__)


class OpenAPIMcpService:
    """
    OpenAPI仕様からMCPサーバーを動的に生成するサービス

    機能:
    - OpenAPI仕様の解析
    - エンドポイントからMCPツール定義の生成
    - ツール実行時のHTTPプロキシ
    - $ref, allOf, oneOf, anyOf などの複合スキーマ解決
    """

    # HTTP リクエストのデフォルトタイムアウト（秒）
    DEFAULT_TIMEOUT = 30.0
    # レスポンスボディの最大サイズ（バイト） - 10MB
    MAX_RESPONSE_SIZE = 10 * 1024 * 1024
    # $ref解決の最大深度（無限ループ防止）
    MAX_REF_DEPTH = 10

    def __init__(
        self,
        openapi_spec: dict[str, Any],
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        server_name: str = "openapi",
        verify_ssl: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """
        初期化

        Args:
            openapi_spec: OpenAPI仕様（JSON/dict形式）
            base_url: ベースURL（仕様のserversを上書き）
            headers: リクエストに追加するヘッダー
            server_name: MCPサーバー名
            verify_ssl: SSL証明書を検証するかどうか（本番環境ではTrue推奨）
            timeout: HTTPリクエストのタイムアウト（秒）
        """
        self.openapi_spec = openapi_spec
        self.headers = headers or {}
        self.server_name = server_name
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        # ベースURLの決定
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            servers = openapi_spec.get("servers", [])
            if servers and isinstance(servers[0], dict):
                self.base_url = servers[0].get("url", "").rstrip("/")
            else:
                self.base_url = ""

        # ベースURLのバリデーション
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            logger.warning(
                "Invalid base_url: must start with http:// or https://",
                base_url=self.base_url,
                server_name=server_name,
            )
            self.base_url = ""

        # ツール定義をキャッシュ
        self._tool_definitions: list[dict[str, Any]] = []
        self._operation_map: dict[str, dict[str, Any]] = {}

    def parse_spec(self) -> list[dict[str, Any]]:
        """
        OpenAPI仕様を解析してMCPツール定義を生成

        Returns:
            MCPツール定義のリスト
        """
        self._tool_definitions = []
        self._operation_map = {}

        paths = self.openapi_spec.get("paths", {})

        for path, path_item in paths.items():
            # path_item が dict でない場合はスキップ
            if not isinstance(path_item, dict):
                logger.warning(
                    "Invalid path item, skipping",
                    path=path,
                    server_name=self.server_name,
                )
                continue

            for method in ["get", "post", "put", "patch", "delete"]:
                if method not in path_item:
                    continue

                operation = path_item[method]
                if not isinstance(operation, dict):
                    logger.warning(
                        "Invalid operation, skipping",
                        path=path,
                        method=method,
                        server_name=self.server_name,
                    )
                    continue

                operation_id = operation.get("operationId")

                if not operation_id:
                    # operationIdがない場合は自動生成
                    operation_id = self._generate_operation_id(method, path)

                # operationIdの重複チェック
                if operation_id in self._operation_map:
                    logger.warning(
                        "Duplicate operationId detected, overwriting previous definition",
                        operation_id=operation_id,
                        new_path=path,
                        new_method=method,
                        previous_path=self._operation_map[operation_id]["path"],
                        server_name=self.server_name,
                    )

                # ツール定義を作成
                tool_def = self._create_tool_definition(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    operation=operation,
                )

                self._tool_definitions.append(tool_def)

                # オペレーションマップに保存（実行時に使用）
                self._operation_map[operation_id] = {
                    "method": method.upper(),
                    "path": path,
                    "operation": operation,
                }

        return self._tool_definitions

    def _generate_operation_id(self, method: str, path: str) -> str:
        """パスからoperationIdを生成"""
        # /api/bundle/{bundleId}/page/{pageId} -> api_bundle_bundleId_page_pageId
        clean_path = path.replace("{", "").replace("}", "")
        clean_path = re.sub(r"[^a-zA-Z0-9/]", "", clean_path)
        parts = [p for p in clean_path.split("/") if p]
        return f"{method}_{'_'.join(parts)}"

    def _resolve_ref(self, ref: str, depth: int = 0) -> dict[str, Any]:
        """
        $refを解決してスキーマを取得

        Args:
            ref: 参照文字列 (例: "#/components/schemas/User")
            depth: 現在の解決深度

        Returns:
            解決されたスキーマ
        """
        if depth > self.MAX_REF_DEPTH:
            logger.warning(
                "$ref解決の最大深度に到達",
                ref=ref,
                max_depth=self.MAX_REF_DEPTH,
            )
            return {"type": "object"}

        if not ref.startswith("#/"):
            logger.warning("外部参照は未サポート", ref=ref)
            return {"type": "object"}

        # パスを分解して辿る
        parts = ref[2:].split("/")
        current = self.openapi_spec

        try:
            for part in parts:
                # URLエンコードされた文字をデコード
                part = part.replace("~1", "/").replace("~0", "~")
                current = current[part]
        except (KeyError, TypeError) as e:
            logger.warning(
                "$ref解決エラー",
                ref=ref,
                error=str(e),
            )
            return {"type": "object"}

        # 解決結果自体に$refがある場合は再帰的に解決
        if isinstance(current, dict) and "$ref" in current:
            return self._resolve_ref(current["$ref"], depth + 1)

        return current if isinstance(current, dict) else {"type": "object"}

    def _resolve_schema(self, schema: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        """
        スキーマを解決（$ref, allOf, oneOf, anyOf を処理）

        Args:
            schema: 解決するスキーマ
            depth: 現在の解決深度

        Returns:
            解決されたスキーマ
        """
        if depth > self.MAX_REF_DEPTH:
            logger.warning("スキーマ解決の最大深度に到達")
            return schema

        if not isinstance(schema, dict):
            return schema

        # $refの解決
        if "$ref" in schema:
            resolved = self._resolve_ref(schema["$ref"], depth)
            # 他のプロパティがある場合はマージ
            other_props = {k: v for k, v in schema.items() if k != "$ref"}
            if other_props:
                return self._merge_schemas([resolved, other_props], depth + 1)
            return self._resolve_schema(resolved, depth + 1)

        # allOfの解決（全てのスキーマをマージ）
        if "allOf" in schema:
            all_schemas = [
                self._resolve_schema(s, depth + 1) for s in schema["allOf"]
            ]
            merged = self._merge_schemas(all_schemas, depth + 1)
            # allOf以外のプロパティもマージ
            other_props = {k: v for k, v in schema.items() if k != "allOf"}
            if other_props:
                merged = self._merge_schemas([merged, other_props], depth + 1)
            return merged

        # oneOf/anyOfの解決（最初のスキーマを採用）
        for keyword in ("oneOf", "anyOf"):
            if keyword in schema:
                if schema[keyword]:
                    first_schema = self._resolve_schema(schema[keyword][0], depth + 1)
                    # 他のプロパティもマージ
                    other_props = {k: v for k, v in schema.items() if k != keyword}
                    if other_props:
                        return self._merge_schemas([first_schema, other_props], depth + 1)
                    return first_schema
                return {"type": "object"}

        # propertiesの各値を再帰的に解決
        if "properties" in schema:
            resolved_props = {}
            for prop_name, prop_schema in schema["properties"].items():
                resolved_props[prop_name] = self._resolve_schema(prop_schema, depth + 1)
            schema = {**schema, "properties": resolved_props}

        # itemsを再帰的に解決
        if "items" in schema:
            schema = {**schema, "items": self._resolve_schema(schema["items"], depth + 1)}

        return schema

    def _merge_schemas(self, schemas: list[dict[str, Any]], depth: int = 0) -> dict[str, Any]:
        """
        複数のスキーマをマージ

        Args:
            schemas: マージするスキーマのリスト
            depth: 現在の解決深度

        Returns:
            マージされたスキーマ
        """
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

        for schema in schemas:
            if not isinstance(schema, dict):
                continue

            # typeの設定（最後のものが優先）
            if "type" in schema:
                merged["type"] = schema["type"]

            # propertiesのマージ
            if "properties" in schema:
                merged["properties"].update(schema["properties"])

            # requiredのマージ
            if "required" in schema:
                for req in schema["required"]:
                    if req not in merged["required"]:
                        merged["required"].append(req)

            # descriptionのマージ
            if "description" in schema:
                merged["description"] = schema["description"]

        # 空のリストは削除
        if not merged["required"]:
            del merged["required"]

        return merged

    def _create_tool_definition(
        self,
        operation_id: str,
        method: str,
        path: str,
        operation: dict[str, Any],
    ) -> dict[str, Any]:
        """
        OpenAPIオペレーションからMCPツール定義を作成

        Args:
            operation_id: オペレーションID
            method: HTTPメソッド
            path: パス
            operation: OpenAPIオペレーション定義

        Returns:
            MCPツール定義
        """
        # 説明の構築
        summary = operation.get("summary", "")
        description = operation.get("description", "")
        full_description = f"{summary}\n\n{description}".strip() if description else summary
        if not full_description:
            full_description = f"{method} {path}"

        # パラメータからinput_schemaを構築
        properties = {}
        required = []

        # パスパラメータ
        path_params = re.findall(r"\{(\w+)\}", path)

        # パラメータ定義
        for param in operation.get("parameters", []):
            param_name = param.get("name")
            param_in = param.get("in")  # query, path, header
            param_required = param.get("required", False)
            param_schema = param.get("schema", {"type": "string"})
            param_description = param.get("description", "")

            # ヘッダーパラメータはスキップ（headersで処理）
            if param_in == "header":
                continue

            # パラメータスキーマを解決（$ref対応）
            resolved_param_schema = self._resolve_schema(param_schema) if isinstance(param_schema, dict) else {"type": "string"}

            properties[param_name] = {
                "type": resolved_param_schema.get("type", "string"),
                "description": f"{param_description} ({param_in} parameter)",
            }

            if resolved_param_schema.get("default") is not None:
                properties[param_name]["default"] = resolved_param_schema["default"]

            if param_required or param_name in path_params:
                required.append(param_name)

        # リクエストボディ
        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            body_schema = json_content.get("schema", {})

            if body_schema:
                # スキーマを解決（$ref, allOf等）
                resolved_schema = self._resolve_schema(body_schema)

                # ボディのプロパティをマージ
                body_props = resolved_schema.get("properties", {})
                for prop_name, prop_def in body_props.items():
                    # ネストしたスキーマも解決
                    resolved_prop = self._resolve_schema(prop_def) if isinstance(prop_def, dict) else prop_def
                    properties[prop_name] = {
                        "type": resolved_prop.get("type", "string") if isinstance(resolved_prop, dict) else "string",
                        "description": resolved_prop.get("description", f"Request body field: {prop_name}") if isinstance(resolved_prop, dict) else f"Request body field: {prop_name}",
                    }

                # 必須フィールド
                body_required = resolved_schema.get("required", [])
                required.extend(body_required)

        return {
            "name": operation_id,
            "description": full_description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(set(required)),  # 重複を除去
            },
        }

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """
        ツール定義を取得（キャッシュがなければ解析を実行）

        Returns:
            MCPツール定義のリスト
        """
        if not self._tool_definitions:
            self.parse_spec()
        return self._tool_definitions

    def get_allowed_tools(self) -> list[str]:
        """
        許可ツール名のリストを取得

        Returns:
            ツール名リスト（mcp__{server_name}__{tool_name}形式）
        """
        tools = self.get_tool_definitions()
        return [f"mcp__{self.server_name}__{t['name']}" for t in tools]

    def _encode_path_parameter(self, value: Any) -> str:
        """
        パスパラメータを安全にURLエンコードする

        Args:
            value: エンコードする値

        Returns:
            URLエンコードされた文字列
        """
        # Noneの場合は空文字列
        if value is None:
            return ""
        # 文字列に変換してURLエンコード（safe='' で全ての特殊文字をエンコード）
        return quote(str(value), safe="")

    async def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        additional_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        ツールを実行（HTTP APIを呼び出す）

        Args:
            tool_name: ツール名（operationId）
            args: ツール引数
            additional_headers: 追加ヘッダー

        Returns:
            ツール実行結果
        """
        if tool_name not in self._operation_map:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "is_error": True,
            }

        # base_urlが設定されていない場合はエラー
        if not self.base_url:
            logger.error(
                "Cannot execute tool: base_url is not configured",
                tool_name=tool_name,
                server_name=self.server_name,
            )
            return {
                "content": [{"type": "text", "text": "API base URL is not configured"}],
                "is_error": True,
            }

        op_info = self._operation_map[tool_name]
        method = op_info["method"]
        path_template = op_info["path"]
        operation = op_info["operation"]

        # パスパラメータを置換（URLエンコード付き）
        path = path_template
        path_params = re.findall(r"\{(\w+)\}", path_template)
        for param_name in path_params:
            if param_name in args:
                # パスパラメータをURLエンコード
                encoded_value = self._encode_path_parameter(args[param_name])
                path = path.replace(f"{{{param_name}}}", encoded_value)
            else:
                return {
                    "content": [{"type": "text", "text": f"Missing required path parameter: {param_name}"}],
                    "is_error": True,
                }

        # クエリパラメータを抽出
        query_params = {}
        body_data = {}

        for param in operation.get("parameters", []):
            param_name = param.get("name")
            param_in = param.get("in")

            if param_name in args:
                if param_in == "query":
                    query_params[param_name] = args[param_name]

        # リクエストボディを構築
        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            if "application/json" in content:
                body_schema = content["application/json"].get("schema", {})
                body_props = body_schema.get("properties", {})
                for prop_name in body_props:
                    if prop_name in args:
                        body_data[prop_name] = args[prop_name]

        # ヘッダーをマージ
        headers = dict(self.headers)
        if additional_headers:
            headers.update(additional_headers)

        # HTTPリクエストを実行
        url = f"{self.base_url}{path}"

        try:
            async with httpx.AsyncClient(
                verify=self.verify_ssl,
                timeout=self.timeout,
            ) as client:
                if method == "GET":
                    response = await client.get(url, params=query_params, headers=headers)
                elif method == "POST":
                    # 空の辞書は json={} として送信（None ではなく）
                    response = await client.post(
                        url,
                        params=query_params,
                        json=body_data if body_data else None,
                        headers=headers,
                    )
                elif method == "PUT":
                    response = await client.put(
                        url,
                        params=query_params,
                        json=body_data if body_data else None,
                        headers=headers,
                    )
                elif method == "PATCH":
                    response = await client.patch(
                        url,
                        params=query_params,
                        json=body_data if body_data else None,
                        headers=headers,
                    )
                elif method == "DELETE":
                    response = await client.delete(url, params=query_params, headers=headers)
                else:
                    return {
                        "content": [{"type": "text", "text": f"Unsupported HTTP method: {method}"}],
                        "is_error": True,
                    }

                response.raise_for_status()

                # レスポンスサイズをチェック
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > self.MAX_RESPONSE_SIZE:
                    logger.warning(
                        "Response too large, truncating",
                        url=url,
                        content_length=content_length,
                        max_size=self.MAX_RESPONSE_SIZE,
                    )
                    return {
                        "content": [{"type": "text", "text": "Response too large to process"}],
                        "is_error": True,
                    }

                # レスポンスを解析
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    # JSONパースエラーをハンドリング
                    try:
                        result_data = response.json()
                        result_text = self._format_json_response(result_data)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "Failed to parse JSON response",
                            url=url,
                            error=str(e),
                        )
                        result_text = response.text
                else:
                    result_text = response.text

                # 成功時もログを記録
                logger.debug(
                    "OpenAPI tool execution successful",
                    tool_name=tool_name,
                    url=url,
                    method=method,
                    status_code=response.status_code,
                )

                return {
                    "content": [{"type": "text", "text": result_text}],
                    "_metadata": {
                        "status_code": response.status_code,
                        "url": url,
                        "method": method,
                    },
                }

        except httpx.HTTPStatusError as e:
            error_text = f"HTTP {e.response.status_code}: {e.response.text[:500]}"
            logger.error(
                "OpenAPI tool execution HTTP error",
                tool_name=tool_name,
                error=error_text,
                url=url,
                status_code=e.response.status_code,
            )
            return {
                "content": [{"type": "text", "text": error_text}],
                "is_error": True,
            }
        except httpx.TimeoutException as e:
            logger.error(
                "OpenAPI tool execution timeout",
                tool_name=tool_name,
                url=url,
                timeout=self.timeout,
            )
            return {
                "content": [{"type": "text", "text": f"Request timeout after {self.timeout}s"}],
                "is_error": True,
            }
        except httpx.ConnectError as e:
            logger.error(
                "OpenAPI tool connection error",
                tool_name=tool_name,
                url=url,
                error=str(e),
            )
            return {
                "content": [{"type": "text", "text": f"Connection error: {str(e)}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.error(
                "OpenAPI tool execution error",
                tool_name=tool_name,
                error=str(e),
                url=url,
                exc_info=True,
            )
            return {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "is_error": True,
            }

    def _format_json_response(self, data: Any, indent: int = 2) -> str:
        """JSONレスポンスを読みやすい形式にフォーマット"""
        return json.dumps(data, ensure_ascii=False, indent=indent)

    def create_tool_handler(self, tool_name: str):
        """
        特定のツール用のハンドラー関数を作成

        Args:
            tool_name: ツール名

        Returns:
            非同期ハンドラー関数
        """
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            return await self.execute_tool(tool_name, args)

        return handler


def create_openapi_mcp_server(
    openapi_spec: dict[str, Any],
    server_name: str,
    base_url: str | None = None,
    headers: dict[str, str] | None = None,
    verify_ssl: bool = True,
    timeout: float = OpenAPIMcpService.DEFAULT_TIMEOUT,
):
    """
    OpenAPI仕様からSDK MCPサーバーを作成

    Args:
        openapi_spec: OpenAPI仕様
        server_name: サーバー名
        base_url: ベースURL
        headers: ヘッダー
        verify_ssl: SSL証明書を検証するかどうか（本番環境ではTrue推奨）
        timeout: HTTPリクエストのタイムアウト（秒）

    Returns:
        SDK MCPサーバーとサービスのタプル（SDKが利用できない場合はNone）
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping OpenAPI MCP server")
        return None

    # OpenAPIサービスを初期化
    service = OpenAPIMcpService(
        openapi_spec=openapi_spec,
        base_url=base_url,
        headers=headers,
        server_name=server_name,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )

    # ツール定義を取得
    tool_definitions = service.get_tool_definitions()

    if not tool_definitions:
        logger.warning("No tools found in OpenAPI spec", server_name=server_name)
        return None

    # 動的にツールを作成
    def create_tool_function(tool_def: dict, svc: OpenAPIMcpService):
        """クロージャ問題を回避するためのファクトリ関数"""
        t_name = tool_def["name"]
        t_description = tool_def["description"]
        input_schema = tool_def["input_schema"]

        # 入力スキーマをSDK用に変換
        schema_dict = {}
        for prop_name, prop_def in input_schema.get("properties", {}).items():
            prop_type = prop_def.get("type", "string")
            if prop_type == "string":
                schema_dict[prop_name] = str
            elif prop_type == "integer":
                schema_dict[prop_name] = int
            elif prop_type == "number":
                schema_dict[prop_name] = float
            elif prop_type == "boolean":
                schema_dict[prop_name] = bool
            else:
                schema_dict[prop_name] = str

        # ハンドラーを作成
        handler = svc.create_tool_handler(t_name)

        # ツールデコレータを適用
        @tool(t_name, t_description, schema_dict)
        async def tool_func(args: dict[str, Any]) -> dict[str, Any]:
            return await handler(args)

        return tool_func

    tools = [create_tool_function(td, service) for td in tool_definitions]

    # MCPサーバーを作成
    server = create_sdk_mcp_server(
        name=server_name,
        version="1.0.0",
        tools=tools,
    )

    return server, service
