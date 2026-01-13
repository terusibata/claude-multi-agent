"""
OpenAPI仕様からMCPサーバーを動的に生成するサービス

OpenAPI仕様を解析し、各エンドポイントをMCPツールに変換。
ツール実行時にHTTP APIを呼び出すプロキシとして機能。
"""
import re
from typing import Any, Optional
from urllib.parse import urljoin

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
    """

    def __init__(
        self,
        openapi_spec: dict[str, Any],
        base_url: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        server_name: str = "openapi",
    ):
        """
        初期化

        Args:
            openapi_spec: OpenAPI仕様（JSON/dict形式）
            base_url: ベースURL（仕様のserversを上書き）
            headers: リクエストに追加するヘッダー
            server_name: MCPサーバー名
        """
        self.openapi_spec = openapi_spec
        self.headers = headers or {}
        self.server_name = server_name

        # ベースURLの決定
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            servers = openapi_spec.get("servers", [])
            if servers:
                self.base_url = servers[0].get("url", "").rstrip("/")
            else:
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
            for method in ["get", "post", "put", "patch", "delete"]:
                if method not in path_item:
                    continue

                operation = path_item[method]
                operation_id = operation.get("operationId")

                if not operation_id:
                    # operationIdがない場合は自動生成
                    operation_id = self._generate_operation_id(method, path)

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

            properties[param_name] = {
                "type": param_schema.get("type", "string"),
                "description": f"{param_description} ({param_in} parameter)",
            }

            if param_schema.get("default") is not None:
                properties[param_name]["default"] = param_schema["default"]

            if param_required or param_name in path_params:
                required.append(param_name)

        # リクエストボディ
        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            body_schema = json_content.get("schema", {})

            if body_schema:
                # ボディのプロパティをマージ
                body_props = body_schema.get("properties", {})
                for prop_name, prop_def in body_props.items():
                    properties[prop_name] = {
                        "type": prop_def.get("type", "string"),
                        "description": prop_def.get("description", f"Request body field: {prop_name}"),
                    }

                # 必須フィールド
                body_required = body_schema.get("required", [])
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

    async def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        additional_headers: Optional[dict[str, str]] = None,
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

        op_info = self._operation_map[tool_name]
        method = op_info["method"]
        path_template = op_info["path"]
        operation = op_info["operation"]

        # パスパラメータを置換
        path = path_template
        path_params = re.findall(r"\{(\w+)\}", path_template)
        for param_name in path_params:
            if param_name in args:
                path = path.replace(f"{{{param_name}}}", str(args[param_name]))
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
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                if method == "GET":
                    response = await client.get(url, params=query_params, headers=headers)
                elif method == "POST":
                    response = await client.post(url, params=query_params, json=body_data or None, headers=headers)
                elif method == "PUT":
                    response = await client.put(url, params=query_params, json=body_data or None, headers=headers)
                elif method == "PATCH":
                    response = await client.patch(url, params=query_params, json=body_data or None, headers=headers)
                elif method == "DELETE":
                    response = await client.delete(url, params=query_params, headers=headers)
                else:
                    return {
                        "content": [{"type": "text", "text": f"Unsupported HTTP method: {method}"}],
                        "is_error": True,
                    }

                response.raise_for_status()

                # レスポンスを解析
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    result_data = response.json()
                    result_text = self._format_json_response(result_data)
                else:
                    result_text = response.text

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
            logger.error("OpenAPI tool execution HTTP error", error=error_text, url=url)
            return {
                "content": [{"type": "text", "text": error_text}],
                "is_error": True,
            }
        except Exception as e:
            logger.error("OpenAPI tool execution error", error=str(e), url=url)
            return {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "is_error": True,
            }

    def _format_json_response(self, data: Any, indent: int = 2) -> str:
        """JSONレスポンスを読みやすい形式にフォーマット"""
        import json
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
    base_url: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
):
    """
    OpenAPI仕様からSDK MCPサーバーを作成

    Args:
        openapi_spec: OpenAPI仕様
        server_name: サーバー名
        base_url: ベースURL
        headers: ヘッダー

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
