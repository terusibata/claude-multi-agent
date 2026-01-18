"""
モデル管理API
モデル定義のCRUD操作
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.model import ModelCreate, ModelResponse, ModelUpdate
from app.services.model_service import ModelInUseError, ModelService

router = APIRouter()


@router.get("", response_model=list[ModelResponse], summary="モデル一覧取得")
async def get_models(
    status: Optional[str] = Query(None, description="ステータスフィルター"),
    db: AsyncSession = Depends(get_db),
):
    """
    登録されている全モデル定義を取得します。

    - **status**: active / deprecated でフィルタリング
    """
    service = ModelService(db)
    return await service.get_all(status=status)


@router.get("/{model_id}", response_model=ModelResponse, summary="モデル詳細取得")
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    指定したIDのモデル定義を取得します。
    """
    service = ModelService(db)
    model = await service.get_by_id(model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"モデル '{model_id}' が見つかりません",
        )
    return model


@router.post(
    "",
    response_model=ModelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="モデル定義作成",
)
async def create_model(
    model_data: ModelCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    新しいモデル定義を作成します。

    - **model_id**: 内部管理ID（重複不可）
    - **bedrock_model_id**: AWS BedrockのモデルID
    - **input_token_price**: 入力トークン単価（USD/1Kトークン、AWS Bedrock公式価格形式）
    - **output_token_price**: 出力トークン単価（USD/1Kトークン、AWS Bedrock公式価格形式）
    """
    service = ModelService(db)

    # 重複チェック
    existing = await service.get_by_id(model_data.model_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"モデル '{model_data.model_id}' は既に存在します",
        )

    model = await service.create(model_data)
    await db.commit()
    return model


@router.put("/{model_id}", response_model=ModelResponse, summary="モデル定義更新")
async def update_model(
    model_id: str,
    model_data: ModelUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    モデル定義を更新します（単価変更等）。
    """
    service = ModelService(db)
    model = await service.update(model_id, model_data)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"モデル '{model_id}' が見つかりません",
        )
    await db.commit()
    return model


@router.patch("/{model_id}/status", response_model=ModelResponse, summary="ステータス変更")
async def update_model_status(
    model_id: str,
    status_value: str = Query(..., alias="status", pattern="^(active|deprecated)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    モデルのステータスを変更します。

    - **active**: 利用可能
    - **deprecated**: 非推奨（新規実行不可）
    """
    service = ModelService(db)
    model = await service.update_status(model_id, status_value)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"モデル '{model_id}' が見つかりません",
        )
    await db.commit()
    return model


@router.delete(
    "/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="モデル定義削除",
)
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    モデル定義を削除します。

    **注意**: 以下の条件をすべて満たす場合のみ削除可能です：
    - テナントのデフォルトモデルとして使用されていない
    - 会話で使用されていない
    - 使用量ログに記録がない
    """
    service = ModelService(db)

    try:
        deleted = await service.delete(model_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"モデル '{model_id}' が見つかりません",
            )
        await db.commit()
    except ModelInUseError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": e.message,
                "usage": e.usage_details,
            },
        )
