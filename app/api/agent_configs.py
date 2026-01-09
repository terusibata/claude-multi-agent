"""
エージェント実行設定API
テナントごとのエージェント設定のCRUD操作
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.agent_config import (
    AgentConfigCreate,
    AgentConfigResponse,
    AgentConfigUpdate,
)
from app.services.agent_config_service import AgentConfigService
from app.services.model_service import ModelService

router = APIRouter()


@router.get("", response_model=list[AgentConfigResponse], summary="設定一覧取得")
async def get_agent_configs(
    tenant_id: str,
    status: Optional[str] = Query(None, description="ステータスフィルター"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントのエージェント実行設定一覧を取得します。
    """
    service = AgentConfigService(db)
    return await service.get_all_by_tenant(tenant_id, status=status)


@router.get("/{config_id}", response_model=AgentConfigResponse, summary="設定詳細取得")
async def get_agent_config(
    tenant_id: str,
    config_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    指定したIDのエージェント実行設定を取得します。
    """
    service = AgentConfigService(db)
    config = await service.get_by_id(config_id, tenant_id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"設定 '{config_id}' が見つかりません",
        )
    return config


@router.post(
    "",
    response_model=AgentConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="設定作成",
)
async def create_agent_config(
    tenant_id: str,
    config_data: AgentConfigCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    新しいエージェント実行設定を作成します。

    - **name**: 設定名
    - **system_prompt**: システムプロンプト
    - **model_id**: 使用するモデルID
    - **allowed_tools**: 許可するツールのリスト
    - **permission_mode**: 権限モード
    """
    # モデル存在チェック
    model_service = ModelService(db)
    model = await model_service.get_by_id(config_data.model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{config_data.model_id}' が見つかりません",
        )
    if model.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{config_data.model_id}' は非推奨です",
        )

    service = AgentConfigService(db)
    return await service.create(tenant_id, config_data)


@router.put("/{config_id}", response_model=AgentConfigResponse, summary="設定更新")
async def update_agent_config(
    tenant_id: str,
    config_id: str,
    config_data: AgentConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    エージェント実行設定を更新します。
    """
    # モデル存在チェック（指定されている場合）
    if config_data.model_id:
        model_service = ModelService(db)
        model = await model_service.get_by_id(config_data.model_id)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"モデル '{config_data.model_id}' が見つかりません",
            )
        if model.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"モデル '{config_data.model_id}' は非推奨です",
            )

    service = AgentConfigService(db)
    config = await service.update(config_id, tenant_id, config_data)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"設定 '{config_id}' が見つかりません",
        )
    return config


@router.delete(
    "/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="設定削除",
)
async def delete_agent_config(
    tenant_id: str,
    config_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    エージェント実行設定を削除します。
    """
    service = AgentConfigService(db)
    deleted = await service.delete(config_id, tenant_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"設定 '{config_id}' が見つかりません",
        )
