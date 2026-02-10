"""
MCPサーバー管理API
テナントごとのMCPサーバー設定のCRUD操作
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_mcp_server_or_404
from app.database import get_db
from app.models.mcp_server import McpServer
from app.schemas.mcp_server import (
    McpServerCreate,
    McpServerResponse,
    McpServerUpdate,
)
from app.services.mcp_server_service import McpServerService
from app.utils.error_handler import raise_not_found

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


@router.get("/builtin", summary="ビルトインMCPサーバー一覧取得")
async def get_builtin_mcp_servers(
    db: AsyncSession = Depends(get_db),
):
    """
    利用可能なビルトインMCPサーバーの一覧を取得します。
    """
    service = McpServerService(db)
    return service.get_all_builtin_servers()


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
    - **type**: http / sse / stdio / builtin / openapi
    - **url**: サーバーURL（http/sseの場合）
    - **command**: 起動コマンド（stdioの場合）
    - **tools**: ツール定義リスト（builtinの場合）
    - **openapi_spec**: OpenAPI仕様（openapiの場合）
    - **openapi_base_url**: OpenAPI APIのベースURL（openapiの場合、オプション）
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
    if server_data.type == "builtin" and not server_data.tools:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="タイプ 'builtin' にはツール定義（tools）が必要です",
        )
    if server_data.type == "openapi" and not server_data.openapi_spec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="タイプ 'openapi' にはOpenAPI仕様（openapi_spec）が必要です",
        )
    if server_data.type not in ["http", "sse", "stdio", "builtin", "openapi"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不正なタイプ '{server_data.type}'。http / sse / stdio / builtin / openapi のいずれかを指定してください",
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
