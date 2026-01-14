"""
APIルーター
REST APIエンドポイントの定義
"""
from fastapi import APIRouter

from app.api import (
    agent_configs,
    conversations,
    execute,
    mcp_servers,
    models,
    skills,
    usage,
    workspace,
)

# メインルーター
api_router = APIRouter()

# 各APIルーターを登録
api_router.include_router(
    models.router,
    prefix="/models",
    tags=["モデル管理"],
)

api_router.include_router(
    agent_configs.router,
    prefix="/tenants/{tenant_id}/agent-configs",
    tags=["エージェント実行設定"],
)

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
    execute.router,
    prefix="/tenants/{tenant_id}",
    tags=["エージェント実行"],
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
