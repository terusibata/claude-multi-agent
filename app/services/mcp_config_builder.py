"""
MCP設定構築サービス

テナントのMCPサーバー設定を構築する。
AgentCore移行後はプロキシ経由ではなく、ヘッダー付きでコンテナに直接渡す。
Firecracker隔離により、トークンがコンテナ内に存在しても安全。
"""
import re

import structlog

from app.services.mcp_server_service import McpServerService
from app.schemas.execute import ExecuteRequest

logger = structlog.get_logger(__name__)


class McpConfigBuilder:
    """MCP設定構築"""

    def __init__(
        self,
        mcp_server_service: McpServerService,
    ):
        self._mcp_server_service = mcp_server_service

    async def build_mcp_server_configs(self, request: ExecuteRequest) -> list[dict]:
        """テナントのアクティブ MCP サーバー設定をシリアライズしてコンテナに渡す形式に変換

        AgentCore移行後: resolve_headers() 後のヘッダをそのまま含めて返却。
        プロキシ経由への書き換えは不要（Firecracker隔離で安全）。
        """
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

    @staticmethod
    def compute_allowed_tools(
        request: ExecuteRequest,
        mcp_server_configs: list[dict],
        *,
        has_default_skills: bool = False,
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

        # Skills: preferred_skills またはデフォルトSkillsが存在する場合に有効化
        if request.preferred_skills or has_default_skills:
            allowed_tools.append("Skill")

        return allowed_tools
