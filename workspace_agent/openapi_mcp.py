"""
OpenAPI仕様からMCPサーバーを動的に生成するサービス

OpenAPI仕様を解析し、各エンドポイントをMCPツールに変換。
ツール実行時にHTTP APIを呼び出すプロキシとして機能。
"""
import json
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class OpenAPIMcpService:
    """
    OpenAPI仕様からMCPサーバーを動的に生成するサービス

    機能:
    - OpenAPI仕様の解析
    - エンドポイントからMCPツール定義の生成
    - ツール実行時のHTTPプロキシ
    - $ref, allOf, oneOf, anyOf などの複合スキーマ解決
    """

    DEFAULT_TIMEOUT = 30.0
    MAX_RESPONSE_SIZE = 10 * 1024 * 1024
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
        self.openapi_spec = openapi_spec
        self.headers = headers or {}
        self.server_name = server_name
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            servers = openapi_spec.get("servers", [])
            if servers and isinstance(servers[0], dict):
                self.base_url = servers[0].get("url", "").rstrip("/")
            else:
                self.base_url = ""

        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            logger.warning(
                "Invalid base_url: must start with http:// or https:// (base_url=%s, server=%s)",
                self.base_url,
                server_name,
            )
            self.base_url = ""

        self._tool_definitions: list[dict[str, Any]] = []
        self._operation_map: dict[str, dict[str, Any]] = {}

    def parse_spec(self) -> list[dict[str, Any]]:
        """OpenAPI仕様を解析してMCPツール定義を生成"""
        self._tool_definitions = []
        self._operation_map = {}

        paths = self.openapi_spec.get("paths", {})

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            for method in ["get", "post", "put", "patch", "delete"]:
                if method not in path_item:
                    continue

                operation = path_item[method]
                if not isinstance(operation, dict):
                    continue

                operation_id = operation.get("operationId")
                if not operation_id:
                    operation_id = self._generate_operation_id(method, path)

                tool_def = self._create_tool_definition(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    operation=operation,
                )

                self._tool_definitions.append(tool_def)
                self._operation_map[operation_id] = {
                    "method": method.upper(),
                    "path": path,
                    "operation": operation,
                }

        return self._tool_definitions

    def _generate_operation_id(self, method: str, path: str) -> str:
        clean_path = path.replace("{", "").replace("}", "")
        clean_path = re.sub(r"[^a-zA-Z0-9/]", "", clean_path)
        parts = [p for p in clean_path.split("/") if p]
        return f"{method}_{'_'.join(parts)}"

    def _resolve_ref(self, ref: str, depth: int = 0) -> dict[str, Any]:
        if depth > self.MAX_REF_DEPTH:
            return {"type": "object"}

        if not ref.startswith("#/"):
            return {"type": "object"}

        parts = ref[2:].split("/")
        current = self.openapi_spec

        try:
            for part in parts:
                part = part.replace("~1", "/").replace("~0", "~")
                current = current[part]
        except (KeyError, TypeError):
            return {"type": "object"}

        if isinstance(current, dict) and "$ref" in current:
            return self._resolve_ref(current["$ref"], depth + 1)

        return current if isinstance(current, dict) else {"type": "object"}

    def _resolve_schema(self, schema: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        if depth > self.MAX_REF_DEPTH:
            return schema

        if not isinstance(schema, dict):
            return schema

        if "$ref" in schema:
            resolved = self._resolve_ref(schema["$ref"], depth)
            other_props = {k: v for k, v in schema.items() if k != "$ref"}
            if other_props:
                return self._merge_schemas([resolved, other_props], depth + 1)
            return self._resolve_schema(resolved, depth + 1)

        if "allOf" in schema:
            all_schemas = [
                self._resolve_schema(s, depth + 1) for s in schema["allOf"]
            ]
            merged = self._merge_schemas(all_schemas, depth + 1)
            other_props = {k: v for k, v in schema.items() if k != "allOf"}
            if other_props:
                merged = self._merge_schemas([merged, other_props], depth + 1)
            return merged

        for keyword in ("oneOf", "anyOf"):
            if keyword in schema:
                if schema[keyword]:
                    first_schema = self._resolve_schema(schema[keyword][0], depth + 1)
                    other_props = {k: v for k, v in schema.items() if k != keyword}
                    if other_props:
                        return self._merge_schemas([first_schema, other_props], depth + 1)
                    return first_schema
                return {"type": "object"}

        if "properties" in schema:
            resolved_props = {}
            for prop_name, prop_schema in schema["properties"].items():
                resolved_props[prop_name] = self._resolve_schema(prop_schema, depth + 1)
            schema = {**schema, "properties": resolved_props}

        if "items" in schema:
            schema = {**schema, "items": self._resolve_schema(schema["items"], depth + 1)}

        return schema

    def _merge_schemas(self, schemas: list[dict[str, Any]], depth: int = 0) -> dict[str, Any]:
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

        for schema in schemas:
            if not isinstance(schema, dict):
                continue
            if "type" in schema:
                merged["type"] = schema["type"]
            if "properties" in schema:
                merged["properties"].update(schema["properties"])
            if "required" in schema:
                for req in schema["required"]:
                    if req not in merged["required"]:
                        merged["required"].append(req)
            if "description" in schema:
                merged["description"] = schema["description"]

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
        summary = operation.get("summary", "")
        description = operation.get("description", "")
        full_description = f"{summary}\n\n{description}".strip() if description else summary
        if not full_description:
            full_description = f"{method} {path}"

        properties = {}
        required = []

        path_params = re.findall(r"\{(\w+)\}", path)

        for param in operation.get("parameters", []):
            param_name = param.get("name")
            param_in = param.get("in")
            param_required = param.get("required", False)
            param_schema = param.get("schema", {"type": "string"})
            param_description = param.get("description", "")

            if param_in == "header":
                continue

            resolved_param_schema = self._resolve_schema(param_schema) if isinstance(param_schema, dict) else {"type": "string"}

            properties[param_name] = {
                "type": resolved_param_schema.get("type", "string"),
                "description": f"{param_description} ({param_in} parameter)",
            }

            if resolved_param_schema.get("default") is not None:
                properties[param_name]["default"] = resolved_param_schema["default"]

            if param_required or param_name in path_params:
                required.append(param_name)

        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            body_schema = json_content.get("schema", {})

            if body_schema:
                resolved_schema = self._resolve_schema(body_schema)
                body_props = resolved_schema.get("properties", {})
                for prop_name, prop_def in body_props.items():
                    resolved_prop = self._resolve_schema(prop_def) if isinstance(prop_def, dict) else prop_def
                    properties[prop_name] = {
                        "type": resolved_prop.get("type", "string") if isinstance(resolved_prop, dict) else "string",
                        "description": resolved_prop.get("description", f"Request body field: {prop_name}") if isinstance(resolved_prop, dict) else f"Request body field: {prop_name}",
                    }

                body_required = resolved_schema.get("required", [])
                required.extend(body_required)

        return {
            "name": operation_id,
            "description": full_description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(set(required)),
            },
        }

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        if not self._tool_definitions:
            self.parse_spec()
        return self._tool_definitions

    def get_allowed_tools(self) -> list[str]:
        tools = self.get_tool_definitions()
        return [f"mcp__{self.server_name}__{t['name']}" for t in tools]

    def _encode_path_parameter(self, value: Any) -> str:
        if value is None:
            return ""
        return quote(str(value), safe="")

    async def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        additional_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if tool_name not in self._operation_map:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "is_error": True,
            }

        if not self.base_url:
            return {
                "content": [{"type": "text", "text": "API base URL is not configured"}],
                "is_error": True,
            }

        op_info = self._operation_map[tool_name]
        method = op_info["method"]
        path_template = op_info["path"]
        operation = op_info["operation"]

        path = path_template
        path_params = re.findall(r"\{(\w+)\}", path_template)
        for param_name in path_params:
            if param_name in args:
                encoded_value = self._encode_path_parameter(args[param_name])
                path = path.replace(f"{{{param_name}}}", encoded_value)
            else:
                return {
                    "content": [{"type": "text", "text": f"Missing required path parameter: {param_name}"}],
                    "is_error": True,
                }

        query_params = {}
        body_data = {}

        for param in operation.get("parameters", []):
            param_name = param.get("name")
            param_in = param.get("in")
            if param_name in args:
                if param_in == "query":
                    query_params[param_name] = args[param_name]

        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            if "application/json" in content:
                body_schema = content["application/json"].get("schema", {})
                body_props = body_schema.get("properties", {})
                for prop_name in body_props:
                    if prop_name in args:
                        body_data[prop_name] = args[prop_name]

        headers = dict(self.headers)
        if additional_headers:
            headers.update(additional_headers)

        url = f"{self.base_url}{path}"

        try:
            async with httpx.AsyncClient(
                verify=self.verify_ssl,
                timeout=self.timeout,
            ) as client:
                supported_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
                if method not in supported_methods:
                    return {
                        "content": [{"type": "text", "text": f"Unsupported HTTP method: {method}"}],
                        "is_error": True,
                    }

                json_body = body_data if body_data and method not in ("GET", "DELETE") else None

                response = await client.request(
                    method=method,
                    url=url,
                    params=query_params,
                    json=json_body,
                    headers=headers,
                )

                response.raise_for_status()

                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > self.MAX_RESPONSE_SIZE:
                    return {
                        "content": [{"type": "text", "text": "Response too large to process"}],
                        "is_error": True,
                    }

                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    try:
                        result_data = response.json()
                        result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
                    except json.JSONDecodeError:
                        result_text = response.text
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
            return {
                "content": [{"type": "text", "text": error_text}],
                "is_error": True,
            }
        except httpx.TimeoutException:
            return {
                "content": [{"type": "text", "text": f"Request timeout after {self.timeout}s"}],
                "is_error": True,
            }
        except httpx.ConnectError as e:
            return {
                "content": [{"type": "text", "text": f"Connection error: {str(e)}"}],
                "is_error": True,
            }
        except Exception as e:
            logger.error("OpenAPI tool execution error: %s (tool=%s, url=%s)", str(e), tool_name, url)
            return {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "is_error": True,
            }

    def create_tool_handler(self, tool_name: str):
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
    """OpenAPI仕様からSDK MCPサーバーを作成"""
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping OpenAPI MCP server")
        return None

    service = OpenAPIMcpService(
        openapi_spec=openapi_spec,
        base_url=base_url,
        headers=headers,
        server_name=server_name,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )

    tool_definitions = service.get_tool_definitions()

    if not tool_definitions:
        logger.warning("No tools found in OpenAPI spec (server=%s)", server_name)
        return None

    def create_tool_function(tool_def: dict, svc: OpenAPIMcpService):
        t_name = tool_def["name"]
        t_description = tool_def["description"]
        input_schema = tool_def["input_schema"]

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

        handler = svc.create_tool_handler(t_name)

        @tool(t_name, t_description, schema_dict)
        async def tool_func(args: dict[str, Any]) -> dict[str, Any]:
            return await handler(args)

        return tool_func

    tools = [create_tool_function(td, service) for td in tool_definitions]

    server = create_sdk_mcp_server(
        name=server_name,
        version="1.0.0",
        tools=tools,
    )

    return server, service
