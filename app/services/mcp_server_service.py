"""
MCPサーバー定義サービス
テナントごとのMCPサーバー設定のCRUD操作（OpenAPI専用）
"""
import re
from uuid import uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.schemas.mcp_server import McpServerCreate, McpServerUpdate
from app.utils.exceptions import ValidationError

logger = structlog.get_logger(__name__)


class McpServerService:
    """MCPサーバーサービスクラス"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[McpServer], int]:
        """
        テナントの全MCPサーバーを取得

        Args:
            tenant_id: テナントID
            status: フィルタリング用ステータス
            limit: 取得件数（デフォルト50）
            offset: オフセット（デフォルト0）

        Returns:
            (MCPサーバーリスト, 総件数)
        """
        base_filter = McpServer.tenant_id == tenant_id
        status_filter = McpServer.status == status if status else None

        # 総件数を取得
        count_query = select(func.count()).select_from(McpServer).where(base_filter)
        if status_filter is not None:
            count_query = count_query.where(status_filter)
        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        # データ取得
        query = select(McpServer).where(base_filter)
        if status_filter is not None:
            query = query.where(status_filter)
        query = query.order_by(McpServer.name).limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all()), total

    async def get_by_id(
        self,
        mcp_server_id: str,
        tenant_id: str,
    ) -> McpServer | None:
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
        self._validate_server_config(server_data.openapi_spec)

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
        openapi_spec: dict | None,
    ) -> None:
        """
        MCPサーバー設定を検証

        Args:
            openapi_spec: OpenAPI仕様

        Raises:
            ValidationError: 設定が不正な場合
        """
        if not openapi_spec:
            raise ValidationError(
                "openapi_spec",
                "openapi_specが必要です"
            )

    async def update(
        self,
        mcp_server_id: str,
        tenant_id: str,
        server_data: McpServerUpdate,
    ) -> McpServer | None:
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

        # 更新後のopenapi_specを計算
        new_openapi_spec = update_data.get("openapi_spec", server.openapi_spec)

        try:
            self._validate_server_config(openapi_spec=new_openapi_spec)
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
