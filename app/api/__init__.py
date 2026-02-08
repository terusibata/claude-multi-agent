"""
APIルーター
REST APIエンドポイントの定義
"""
from fastapi import APIRouter

from app.api import (
    mcp_servers,
    models,
    skills,
    tenants,
    usage,
    workspace,
)
from app.api.conversations import crud_router as conv_crud_router
from app.api.conversations import stream_router as conv_stream_router
from app.api.simple_chats import crud_router as chat_crud_router
from app.api.simple_chats import stream_router as chat_stream_router

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

# 会話管理API（CRUD + ストリーミング）
api_router.include_router(
    conv_crud_router,
    prefix="/tenants/{tenant_id}/conversations",
    tags=["会話・履歴"],
)
api_router.include_router(
    conv_stream_router,
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

# シンプルチャットAPI（CRUD + ストリーミング）
api_router.include_router(
    chat_crud_router,
    prefix="/tenants/{tenant_id}/simple-chats",
    tags=["シンプルチャット"],
)
api_router.include_router(
    chat_stream_router,
    prefix="/tenants/{tenant_id}/simple-chats",
    tags=["シンプルチャット"],
)
