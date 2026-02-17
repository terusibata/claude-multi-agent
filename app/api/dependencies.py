"""
API共通依存関係
テナント・モデル検証、オーケストレーター取得などの共通ロジック
"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import structlog
from app.database import get_db
from app.models.agent_skill import AgentSkill
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.simple_chat import SimpleChat
from app.models.tenant import Tenant
from app.services.container.orchestrator import ContainerOrchestrator
from app.services.conversation_service import ConversationService
from app.services.mcp_server_service import McpServerService
from app.services.model_service import ModelService
from app.services.simple_chat_service import SimpleChatService
from app.services.skill_service import SkillService
from app.services.tenant_service import TenantService
from app.utils.error_handler import raise_not_found

logger = structlog.get_logger(__name__)


def get_orchestrator(request: Request) -> ContainerOrchestrator:
    """アプリケーション状態からオーケストレーターを取得"""
    return request.app.state.orchestrator


# --- テナント ---


async def get_tenant_or_404(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """テナントを取得（存在しない場合は404）"""
    service = TenantService(db)
    tenant = await service.get_by_id(tenant_id)
    if not tenant:
        raise_not_found("テナント", tenant_id)
    return tenant


async def get_active_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """アクティブなテナントを取得（存在しないか非アクティブなら例外）"""
    tenant = await get_tenant_or_404(tenant_id, db)
    if tenant.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"テナント '{tenant_id}' は現在利用できません",
        )
    return tenant


# --- モデル ---


async def get_model_or_404(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> Model:
    """モデルを取得（存在しない場合は404）"""
    service = ModelService(db)
    model = await service.get_by_id(model_id)
    if not model:
        raise_not_found("モデル", model_id)
    return model


async def get_active_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> Model:
    """アクティブなモデルを取得（存在しないか非アクティブなら例外）"""
    model = await get_model_or_404(model_id, db)
    if model.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{model_id}' は現在利用できません",
        )
    return model


async def get_model_with_fallback(
    model_id: str,
    tenant: Tenant,
    db: AsyncSession,
) -> Model:
    """
    モデルを取得。非推奨の場合はテナントデフォルト→任意のアクティブモデルにフォールバック。
    """
    model = await get_model_or_404(model_id, db)
    if model.status == "active":
        return model

    # 非推奨モデル → フォールバック
    logger.warning(
        "モデルが非推奨のためフォールバック",
        deprecated_model_id=model_id,
    )

    # 1. テナントデフォルトモデルを試行
    if tenant.model_id and tenant.model_id != model_id:
        fallback = await ModelService(db).get_by_id(tenant.model_id)
        if fallback and fallback.status == "active":
            return fallback

    # 2. 任意のアクティブモデルを使用
    active_models = await ModelService(db).get_active_models()
    if active_models:
        return active_models[0]

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="利用可能なアクティブモデルがありません",
    )


# --- 会話 ---


async def get_conversation_or_404(
    conversation_id: str,
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    """会話を取得（存在しない場合は404）"""
    service = ConversationService(db)
    conversation = await service.get_conversation_by_id(conversation_id, tenant_id)
    if not conversation:
        raise_not_found("会話", conversation_id)
    return conversation


# --- シンプルチャット ---


async def get_simple_chat_or_404(
    chat_id: str,
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> SimpleChat:
    """シンプルチャットを取得（存在しない場合は404）"""
    service = SimpleChatService(db)
    chat = await service.get_chat_by_id(chat_id, tenant_id)
    if not chat:
        raise_not_found("チャット", chat_id)
    return chat


# --- Skill ---


async def get_skill_or_404(
    skill_id: str,
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> AgentSkill:
    """Skillを取得（存在しない場合は404）"""
    service = SkillService(db)
    skill = await service.get_by_id(skill_id, tenant_id)
    if not skill:
        raise_not_found("Skill", skill_id)
    return skill


# --- MCPサーバー ---


async def get_mcp_server_or_404(
    server_id: str,
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> McpServer:
    """MCPサーバーを取得（存在しない場合は404）"""
    service = McpServerService(db)
    server = await service.get_by_id(server_id, tenant_id)
    if not server:
        raise_not_found("MCPサーバー", server_id)
    return server
