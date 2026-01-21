"""
APIルーター
REST APIエンドポイントの定義
"""
from fastapi import APIRouter

from app.api import (
    conversations,
    mcp_servers,
    models,
    simple_chats,
    skills,
    tenants,
    usage,
    workspace,
)

# メインルーター
api_router = APIRouter()

# テナント管理API
api_router.include_router(
    tenants.router,
    tags=["テナント管理"],
)

# モデル管理API
api_router.include_router(
    models.router,
    prefix="/models",
    tags=["モデル管理"],
)

# テナント配下のリソース
api_router.include_router(
    skills.router,
    prefix="/tenants/{tenant_id}/skills",
    tags=["Agent Skills管理"],
)

api_router.include_router(
    mcp_servers.router,
    prefix="/tenants/{tenant_id}/mcp-servers",
    tags=["MCPサーバー管理"],
)

api_router.include_router(
    conversations.router,
    prefix="/tenants/{tenant_id}/conversations",
    tags=["会話・履歴"],
)

api_router.include_router(
    usage.router,
    prefix="/tenants/{tenant_id}",
    tags=["使用状況・コスト"],
)

api_router.include_router(
    workspace.router,
    prefix="/tenants/{tenant_id}",
    tags=["ワークスペース"],
)

api_router.include_router(
    simple_chats.router,
    prefix="/tenants/{tenant_id}/simple-chats",
    tags=["シンプルチャット"],
)
