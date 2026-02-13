"""
MCPサーバー管理API
テナントごとのMCPサーバー設定のCRUD操作（OpenAPI専用）
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_mcp_server_or_404
from app.database import get_db
from app.models.mcp_server import McpServer
from app.schemas.mcp_server import (
    McpServerCreate,
    McpServerListResponse,
    McpServerResponse,
    McpServerUpdate,
)
from app.services.mcp_server_service import McpServerService
from app.utils.error_handler import raise_not_found

router = APIRouter()


@router.get("", response_model=McpServerListResponse, summary="MCPサーバー一覧取得")
async def get_mcp_servers(
    tenant_id: str,
    status: str | None = Query(None, description="ステータスフィルター"),
    limit: int = Query(50, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントのMCPサーバー一覧を取得します。
    """
    service = McpServerService(db)
    servers, total = await service.get_all_by_tenant(
        tenant_id, status=status, limit=limit, offset=offset
    )
    return McpServerListResponse(
        items=servers,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{server_id}", response_model=McpServerResponse, summary="MCPサーバー詳細取得")
async def get_mcp_server(
    server: McpServer = Depends(get_mcp_server_or_404),
):
    """
    指定したIDのMCPサーバーを取得します。
    """
    return server


@router.post(
    "",
    response_model=McpServerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="MCPサーバー登録",
)
async def create_mcp_server(
    tenant_id: str,
    server_data: McpServerCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    新しいMCPサーバーを登録します。

    - **name**: MCPサーバー名（識別子）
    - **openapi_spec**: OpenAPI仕様（JSON形式、必須）
    - **openapi_base_url**: OpenAPI APIのベースURL（オプション）
    - **headers_template**: ヘッダーテンプレート（例: {"Authorization": "Bearer ${token}"}）
    """
    service = McpServerService(db)
    return await service.create(tenant_id, server_data)


@router.put("/{server_id}", response_model=McpServerResponse, summary="MCPサーバー更新")
async def update_mcp_server(
    tenant_id: str,
    server_id: str,
    server_data: McpServerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    MCPサーバー設定を更新します。
    """
    service = McpServerService(db)
    server = await service.update(server_id, tenant_id, server_data)
    if not server:
        raise_not_found("MCPサーバー", server_id)
    return server


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="MCPサーバー削除",
)
async def delete_mcp_server(
    tenant_id: str,
    server_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    MCPサーバーを削除します。
    """
    service = McpServerService(db)
    deleted = await service.delete(server_id, tenant_id)
    if not deleted:
        raise_not_found("MCPサーバー", server_id)
