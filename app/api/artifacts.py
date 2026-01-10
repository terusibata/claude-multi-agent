"""
アーティファクトAPI
セッション内で生成されたファイル・コンテンツの管理
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.artifact import Artifact
from app.models.chat_session import ChatSession
from app.schemas.artifact import ArtifactListResponse, ArtifactResponse
from app.services.artifact_storage_service import ArtifactStorageService

router = APIRouter()


@router.get(
    "/api/tenants/{tenant_id}/sessions/{session_id}/artifacts",
    response_model=ArtifactListResponse,
    summary="セッションのアーティファクト一覧取得",
    description="指定されたセッション内で生成されたアーティファクト（ファイル）の一覧を取得します。",
)
async def get_session_artifacts(
    tenant_id: str = Path(..., description="テナントID"),
    session_id: str = Path(..., description="チャットセッションID"),
    limit: int = Query(50, ge=1, le=200, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    artifact_type: Optional[str] = Query(None, description="フィルタリング用アーティファクトタイプ"),
    db: AsyncSession = Depends(get_db),
):
    """セッションのアーティファクト一覧を取得"""
    # セッションの存在確認と権限チェック
    session_query = select(ChatSession).where(
        ChatSession.chat_session_id == session_id,
        ChatSession.tenant_id == tenant_id,
    )
    session_result = await db.execute(session_query)
    session = session_result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # アーティファクトを取得
    query = select(Artifact).where(Artifact.chat_session_id == session_id)

    # タイプフィルタ
    if artifact_type:
        query = query.where(Artifact.artifact_type == artifact_type)

    # 総件数取得
    count_query = select(func.count()).select_from(query.alias())
    count_result = await db.execute(count_query)
    total_count = count_result.scalar_one()

    # ページネーション
    query = query.order_by(Artifact.created_at.desc())
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    artifacts = result.scalars().all()

    return ArtifactListResponse(
        artifacts=[ArtifactResponse.model_validate(a) for a in artifacts],
        total_count=total_count,
    )


@router.get(
    "/api/tenants/{tenant_id}/artifacts/{artifact_id}",
    response_model=ArtifactResponse,
    summary="アーティファクト詳細取得",
    description="指定されたアーティファクトの詳細情報を取得します。",
)
async def get_artifact(
    tenant_id: str = Path(..., description="テナントID"),
    artifact_id: str = Path(..., description="アーティファクトID"),
    include_content: bool = Query(
        False, description="ファイル内容を含めるかどうか（大きいファイルの場合は注意）"
    ),
    db: AsyncSession = Depends(get_db),
):
    """アーティファクト詳細を取得"""
    # アーティファクトを取得（セッションを通じてテナント権限チェック）
    query = (
        select(Artifact)
        .join(ChatSession, Artifact.chat_session_id == ChatSession.chat_session_id)
        .where(
            Artifact.artifact_id == artifact_id,
            ChatSession.tenant_id == tenant_id,
        )
    )
    result = await db.execute(query)
    artifact = result.scalar_one_or_none()

    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # 内容が要求され、DBに保存されていない場合はストレージから取得
    if include_content and not artifact.content:
        storage_service = ArtifactStorageService()
        try:
            content = await storage_service.read_artifact(
                file_path=artifact.file_path,
                s3_key=artifact.s3_key,
            )
            artifact.content = content
        except Exception as e:
            # 内容取得失敗の場合でも、メタデータは返す
            artifact.content = f"[Failed to load content: {str(e)}]"

    return ArtifactResponse.model_validate(artifact)


@router.delete(
    "/api/tenants/{tenant_id}/artifacts/{artifact_id}",
    status_code=204,
    summary="アーティファクト削除",
    description="指定されたアーティファクトを削除します。",
)
async def delete_artifact(
    tenant_id: str = Path(..., description="テナントID"),
    artifact_id: str = Path(..., description="アーティファクトID"),
    db: AsyncSession = Depends(get_db),
):
    """アーティファクトを削除"""
    # アーティファクトを取得（セッションを通じてテナント権限チェック）
    query = (
        select(Artifact)
        .join(ChatSession, Artifact.chat_session_id == ChatSession.chat_session_id)
        .where(
            Artifact.artifact_id == artifact_id,
            ChatSession.tenant_id == tenant_id,
        )
    )
    result = await db.execute(query)
    artifact = result.scalar_one_or_none()

    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # ストレージから削除
    storage_service = ArtifactStorageService()
    try:
        await storage_service.delete_artifact(
            file_path=artifact.file_path,
            s3_key=artifact.s3_key,
        )
    except Exception as e:
        # ストレージ削除失敗でもDBからは削除する
        pass

    # DBから削除
    await db.delete(artifact)
    await db.commit()

    return None
