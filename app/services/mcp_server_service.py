"""
MCPサーバー定義サービス
テナントごとのMCPサーバー設定のCRUD操作と設定構築
"""
import re
from typing import Any, Optional
from uuid import uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.schemas.mcp_server import McpServerCreate, McpServerUpdate
from app.services.builtin_tools import (
    get_all_builtin_tool_definitions,
    get_builtin_tool_definition,
)
from app.utils.exceptions import ValidationError
from app.utils.security import validate_mcp_command

logger = structlog.get_logger(__name__)


# ビルトインMCPサーバーの定義
BUILTIN_MCP_SERVERS = {
    "file-presentation": {
        "name": "file-presentation",
        "display_name": "ファイル提示サーバー",
        "type": "builtin",
        "description": "AIが作成・編集したファイルをユーザーに提示するためのMCPサーバー",
        "tools": ["present_files"],
        "allowed_tools": ["mcp__file-presentation__present_files"],
    },
}


class McpServerService:
    """MCPサーバーサービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db

    async def get_all_by_tenant(
        self,
        tenant_id: str,
        status: Optional[str] = None,
    ) -> list[McpServer]:
        """
        テナントの全MCPサーバーを取得

        Args:
            tenant_id: テナントID
            status: フィルタリング用ステータス

        Returns:
            MCPサーバーリスト
        """
        query = select(McpServer).where(McpServer.tenant_id == tenant_id)
        if status:
            query = query.where(McpServer.status == status)
        query = query.order_by(McpServer.name)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_id(
        self,
        mcp_server_id: str,
        tenant_id: str,
    ) -> Optional[McpServer]:
        """
        IDでMCPサーバーを取得

        Args:
            mcp_server_id: MCPサーバーID
            tenant_id: テナントID

        Returns:
            MCPサーバー（存在しない場合はNone）
        """
        query = select(McpServer).where(
            McpServer.mcp_server_id == mcp_server_id,
            McpServer.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_ids(
        self,
        mcp_server_ids: list[str],
        tenant_id: str,
    ) -> list[McpServer]:
        """
        複数のIDでMCPサーバーを取得

        Args:
            mcp_server_ids: MCPサーバーIDリスト
            tenant_id: テナントID

        Returns:
            MCPサーバーリスト
        """
        query = select(McpServer).where(
            McpServer.mcp_server_id.in_(mcp_server_ids),
            McpServer.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create(
        self,
        tenant_id: str,
        server_data: McpServerCreate,
    ) -> McpServer:
        """
        MCPサーバーを作成

        Args:
            tenant_id: テナントID
            server_data: 作成データ

        Returns:
            作成されたMCPサーバー

        Raises:
            ValidationError: 作成データが不正な場合
        """
        # タイプ固有のバリデーション
        self._validate_server_config(
            server_type=server_data.type,
            url=server_data.url,
            command=server_data.command,
            openapi_spec=server_data.openapi_spec,
        )

        server = McpServer(
            mcp_server_id=str(uuid4()),
            tenant_id=tenant_id,
            **server_data.model_dump(),
        )
        self.db.add(server)
        await self.db.flush()
        await self.db.refresh(server)
        return server

    def _validate_server_config(
        self,
        server_type: str,
        url: Optional[str],
        command: Optional[str],
        openapi_spec: Optional[dict] = None,
    ) -> None:
        """
        MCPサーバー設定を検証

        Args:
            server_type: サーバータイプ
            url: URL
            command: コマンド
            openapi_spec: OpenAPI仕様

        Raises:
            ValidationError: 設定が不正な場合
        """
        valid_types = {"http", "sse", "stdio", "builtin", "openapi"}
        if server_type not in valid_types:
            raise ValidationError(
                "type",
                f"無効なタイプです。有効なタイプ: {', '.join(valid_types)}"
            )

        # タイプ別の必須フィールド検証
        if server_type in ("http", "sse"):
            if not url:
                raise ValidationError(
                    "url",
                    f"{server_type}タイプにはURLが必要です"
                )
            if not url.startswith(("http://", "https://")):
                raise ValidationError(
                    "url",
                    "URLはhttp://またはhttps://で始まる必要があります"
                )

        elif server_type == "stdio":
            if not command:
                raise ValidationError(
                    "command",
                    "stdioタイプにはcommandが必要です"
                )
            # コマンドインジェクション対策
            validate_mcp_command(command)

        elif server_type == "openapi":
            if not openapi_spec:
                raise ValidationError(
                    "openapi_spec",
                    "openapiタイプにはopenapi_specが必要です"
                )

    async def update(
        self,
        mcp_server_id: str,
        tenant_id: str,
        server_data: McpServerUpdate,
    ) -> Optional[McpServer]:
        """
        MCPサーバーを更新

        Args:
            mcp_server_id: MCPサーバーID
            tenant_id: テナントID
            server_data: 更新データ

        Returns:
            更新されたMCPサーバー（存在しない場合はNone）

        Raises:
            ValidationError: 更新データが不正な場合
        """
        server = await self.get_by_id(mcp_server_id, tenant_id)
        if not server:
            return None

        update_data = server_data.model_dump(exclude_unset=True)

        # 更新後の値を計算（更新データがなければ既存値を使用）
        new_type = update_data.get("type", server.type)
        new_url = update_data.get("url", server.url)
        new_command = update_data.get("command", server.command)
        new_openapi_spec = update_data.get("openapi_spec", server.openapi_spec)

        # タイプ固有のバリデーション
        try:
            self._validate_server_config(
                server_type=new_type,
                url=new_url,
                command=new_command,
                openapi_spec=new_openapi_spec,
            )
        except ValidationError as e:
            logger.warning(
                "MCPサーバー更新バリデーションエラー",
                mcp_server_id=mcp_server_id,
                error=str(e),
            )
            raise

        for field, value in update_data.items():
            setattr(server, field, value)

        await self.db.flush()
        await self.db.refresh(server)
        return server

    async def delete(
        self,
        mcp_server_id: str,
        tenant_id: str,
    ) -> bool:
        """
        MCPサーバーを削除

        Args:
            mcp_server_id: MCPサーバーID
            tenant_id: テナントID

        Returns:
            削除成功かどうか
        """
        server = await self.get_by_id(mcp_server_id, tenant_id)
        if not server:
            return False

        await self.db.delete(server)
        return True

    def _replace_placeholders(
        self,
        template: str,
        tokens: dict[str, str],
    ) -> str:
        """
        プレースホルダーを実際の値に置換

        Args:
            template: テンプレート文字列 (例: "Bearer ${token}")
            tokens: トークン辞書

        Returns:
            置換後の文字列
        """
        def replacer(match: re.Match) -> str:
            key = match.group(1)
            return tokens.get(key, match.group(0))

        return re.sub(r"\$\{(\w+)\}", replacer, template)

    def build_mcp_config(
        self,
        mcp_servers: list[McpServer],
        tokens: dict[str, str],
    ) -> dict[str, Any]:
        """
        MCPサーバー定義からSDK用の設定を構築

        Args:
            mcp_servers: MCPサーバーリスト
            tokens: 認証トークン辞書

        Returns:
            SDK用のMCP設定辞書
        """
        config = {}

        for server in mcp_servers:
            # ヘッダーのプレースホルダーを置換
            headers = {}
            if server.headers_template:
                for key, template in server.headers_template.items():
                    headers[key] = self._replace_placeholders(template, tokens)

            if server.type == "http":
                config[server.name] = {
                    "type": "http",
                    "url": server.url,
                    "headers": headers if headers else None,
                }
            elif server.type == "sse":
                config[server.name] = {
                    "type": "sse",
                    "url": server.url,
                    "headers": headers if headers else None,
                }
            elif server.type == "stdio":
                # コマンドインジェクション対策
                try:
                    validate_mcp_command(server.command, server.args)
                except ValidationError as e:
                    logger.warning(
                        "MCPサーバーコマンド検証エラー",
                        server_name=server.name,
                        command=server.command,
                        error=str(e),
                    )
                    continue  # このサーバーをスキップ

                # 環境変数にトークンを追加
                env = dict(server.env) if server.env else {}
                for token_key, token_value in tokens.items():
                    env[token_key] = token_value

                config[server.name] = {
                    "command": server.command,
                    "args": server.args or [],
                    "env": env if env else None,
                }
            elif server.type == "builtin":
                # builtinタイプはtools定義を含むSDK MCPサーバーとして構築
                # claude_agent_sdkのcreate_sdk_mcp_serverを使用する想定
                tools_definitions = []
                if server.tools:
                    for tool_def in server.tools:
                        if isinstance(tool_def, dict):
                            tools_definitions.append(tool_def)
                        else:
                            # ビルトインツール名の場合、定義を取得
                            builtin_def = get_builtin_tool_definition(tool_def)
                            if builtin_def:
                                tools_definitions.append(builtin_def)

                config[server.name] = {
                    "type": "builtin",
                    "tools": tools_definitions,
                }

            # Noneの値を削除（builtinタイプも含む）
            if server.name in config:
                config[server.name] = {
                    k: v for k, v in config[server.name].items() if v is not None
                }

        return config

    def get_builtin_server_definition(self, server_name: str) -> dict[str, Any] | None:
        """
        ビルトインMCPサーバーの定義を取得

        Args:
            server_name: サーバー名

        Returns:
            サーバー定義（存在しない場合はNone）
        """
        return BUILTIN_MCP_SERVERS.get(server_name)

    def get_all_builtin_servers(self) -> dict[str, dict[str, Any]]:
        """
        全ビルトインMCPサーバーの定義を取得

        Returns:
            サーバー定義の辞書
        """
        return BUILTIN_MCP_SERVERS.copy()

    def get_allowed_tools(
        self,
        mcp_servers: list[McpServer],
    ) -> list[str]:
        """
        MCPサーバーから許可されたツールリストを取得

        Args:
            mcp_servers: MCPサーバーリスト

        Returns:
            許可されたツール名リスト
        """
        tools = []
        for server in mcp_servers:
            if server.allowed_tools:
                tools.extend(server.allowed_tools)
        return tools
