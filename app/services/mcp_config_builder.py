"""
MCP設定構築サービス

テナントのMCPサーバー設定を構築し、コンテナ用にプロキシ経由の設定に変換する。
セキュリティ上、認証トークンはプロキシ側に保持しコンテナには渡さない。
"""
import re

import structlog

from app.services.container.orchestrator import ContainerOrchestrator
from app.services.mcp_server_service import McpServerService
from app.services.proxy.credential_proxy import McpHeaderRule
from app.schemas.execute import ExecuteRequest

logger = structlog.get_logger(__name__)


class McpConfigBuilder:
    """MCP設定構築"""

    def __init__(
        self,
        mcp_server_service: McpServerService,
        orchestrator: ContainerOrchestrator,
    ):
        self._mcp_server_service = mcp_server_service
        self._orchestrator = orchestrator

    async def build_mcp_server_configs(self, request: ExecuteRequest) -> list[dict]:
        """テナントのアクティブ MCP サーバー設定をシリアライズしてコンテナに渡す形式に変換"""
        try:
            mcp_servers, _ = await self._mcp_server_service.get_all_by_tenant(
                request.tenant_id, status="active"
            )
        except Exception as e:
            logger.error("MCP サーバー設定取得エラー", error=str(e))
            return []

        configs = []
        for server in mcp_servers:
            if not server.openapi_spec:
                continue
            # headers_template のトークン解決
            headers = self.resolve_headers(server.headers_template, request.tokens)
            configs.append(
                {
                    "server_name": server.name,
                    "openapi_spec": server.openapi_spec,
                    "base_url": server.openapi_base_url,
                    "headers": headers,
                }
            )
        return configs

    @staticmethod
    def resolve_headers(template: dict | None, tokens: dict[str, str] | None) -> dict:
        """headers_template の ${token} プレースホルダをトークン値で置換"""
        if not template:
            return {}
        resolved = {}
        for key, value in template.items():
            if isinstance(value, str) and tokens:
                resolved[key] = re.sub(
                    r"\$\{(\w+)\}",
                    lambda m: tokens.get(m.group(1), m.group(0)),
                    value,
                )
            else:
                resolved[key] = value
        return resolved

    async def extract_mcp_headers_to_proxy(
        self,
        mcp_server_configs: list[dict],
        container_id: str,
    ) -> list[dict]:
        """MCPサーバー設定からヘッダーを抽出してプロキシに登録し、コンテナ用設定を返す

        トークンを含むヘッダーはプロキシ側に保持し、コンテナには渡さない。
        コンテナに渡すMCP設定ではbase_urlをプロキシローカルに書き換える。

        Args:
            mcp_server_configs: ヘッダー解決済みのMCPサーバー設定リスト
            container_id: コンテナID（プロキシルール登録用）

        Returns:
            コンテナ用MCPサーバー設定リスト（ヘッダーなし、base_urlはプロキシローカル）
        """
        if not mcp_server_configs:
            return []

        # プロキシに登録するMCPヘッダールールを構築
        proxy_rules: dict[str, McpHeaderRule] = {}
        container_configs: list[dict] = []

        for config in mcp_server_configs:
            server_name = config["server_name"]
            original_base_url = config.get("base_url", "")
            headers = config.get("headers", {})

            if original_base_url:
                # プロキシルールに登録（ヘッダー有無問わずプロキシ経由に統一）
                proxy_rules[server_name] = McpHeaderRule(
                    real_base_url=original_base_url,
                    headers=headers,
                )
                # コンテナ用設定: base_urlをプロキシローカルに書き換え、ヘッダーなし
                container_configs.append(
                    {
                        "server_name": server_name,
                        "openapi_spec": config["openapi_spec"],
                        "base_url": f"http://127.0.0.1:8080/mcp/{server_name}",
                    }
                )
            else:
                # base_url なし（無効な設定）→ そのまま渡す
                container_configs.append(
                    {
                        "server_name": server_name,
                        "openapi_spec": config["openapi_spec"],
                        "base_url": "",
                    }
                )

        # プロキシにMCPヘッダールールを登録
        if proxy_rules:
            await self._orchestrator.update_mcp_header_rules(container_id, proxy_rules)

        return container_configs

    @staticmethod
    def compute_allowed_tools(
        request: ExecuteRequest,
        mcp_server_configs: list[dict],
    ) -> list[str]:
        """コンテナに渡す allowed_tools リストを計算"""
        allowed_tools = []

        # ビルトイン MCP サーバー
        allowed_tools.append("mcp__file-tools__*")
        allowed_tools.append("mcp__file-presentation__*")

        # OpenAPI MCP サーバー
        for config in mcp_server_configs:
            server_name = config["server_name"]
            allowed_tools.append(f"mcp__{server_name}__*")

        # preferred_skills のツール
        if request.preferred_skills:
            allowed_tools.append("Skill")

        return allowed_tools
