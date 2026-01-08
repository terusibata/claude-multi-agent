"""
MCPサーバー管理API
テナントごとのMCPサーバー設定のCRUD操作
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.mcp_server import McpServerCreate, McpServerResponse, McpServerUpdate
from app.services.mcp_server_service import McpServerService

router = APIRouter()


@router.get("", response_model=list[McpServerResponse], summary="MCPサーバー一覧取得")
async def get_mcp_servers(
    tenant_id: str,
    status: Optional[str] = Query(None, description="ステータスフィルター"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントのMCPサーバー一覧を取得します。
    """
    service = McpServerService(db)
    return await service.get_all_by_tenant(tenant_id, status=status)


@router.get("/{server_id}", response_model=McpServerResponse, summary="MCPサーバー詳細取得")
async def get_mcp_server(
    tenant_id: str,
    server_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    指定したIDのMCPサーバーを取得します。
    """
    service = McpServerService(db)
    server = await service.get_by_id(server_id, tenant_id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCPサーバー '{server_id}' が見つかりません",
        )
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
    - **type**: http / sse / stdio
    - **url**: サーバーURL（http/sseの場合）
    - **command**: 起動コマンド（stdioの場合）
    - **headers_template**: ヘッダーテンプレート（例: {"Authorization": "Bearer ${token}"}）
    """
    service = McpServerService(db)

    # タイプに応じたバリデーション
    if server_data.type in ["http", "sse"] and not server_data.url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"タイプ '{server_data.type}' にはURLが必要です",
        )
    if server_data.type == "stdio" and not server_data.command:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="タイプ 'stdio' にはコマンドが必要です",
        )

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCPサーバー '{server_id}' が見つかりません",
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCPサーバー '{server_id}' が見つかりません",
        )
