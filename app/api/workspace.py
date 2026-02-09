"""
ワークスペースAPI（S3版）
会話専用ワークスペースのファイル管理

新しいAPI設計:
- GET /conversations/{conversation_id}/files: ファイル一覧
- GET /conversations/{conversation_id}/files/download: ファイルダウンロード
- GET /conversations/{conversation_id}/files/presented: AIが作成したファイル一覧
"""
import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.workspace import (
    PresentedFileList,
    WorkspaceFileList,
)
from app.services.workspace_service import WorkspaceService
from app.utils.exceptions import WorkspaceSecurityError

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/conversations/{conversation_id}/files",
    response_model=WorkspaceFileList,
    summary="ファイル一覧取得",
)
async def list_files(
    tenant_id: str,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    会話のファイル一覧を取得します。

    S3にアップロードされたファイルの一覧をDBから取得して返します。
    """
    workspace_service = WorkspaceService(db)

    try:
        return await workspace_service.list_files(tenant_id, conversation_id)
    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"ファイル一覧取得エラー: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ファイル一覧の取得に失敗しました",
        )


@router.get(
    "/conversations/{conversation_id}/files/download",
    summary="ファイルダウンロード",
)
async def download_file(
    tenant_id: str,
    conversation_id: str,
    path: str = Query(..., description="ファイルパス"),
    db: AsyncSession = Depends(get_db),
):
    """
    ファイルをダウンロードします。

    サーバーがS3からダウンロードしてクライアントに返します。

    ## パラメータ
    - `path`: ファイルパス（例: `uploads/data.csv`、`outputs/result.json`）
    """
    workspace_service = WorkspaceService(db)

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, path
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ファイルが見つかりません",
        )
    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"ファイルダウンロードエラー: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ファイルのダウンロードに失敗しました",
        )

    # ファイル名をサニタイズ（改行・制御文字除去）
    safe_filename = re.sub(r'[\r\n\x00-\x1f]', '', filename)
    # RFC 5987 エンコーディング（非ASCII文字対応）
    encoded_filename = quote(safe_filename, safe='')

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        },
    )


@router.get(
    "/conversations/{conversation_id}/files/presented",
    response_model=PresentedFileList,
    summary="AIが作成したファイル一覧取得",
)
async def get_presented_files(
    tenant_id: str,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    AIがユーザーに提示したファイル（Presented files）の一覧を取得します。

    これらは、AIが作成してユーザーに返したいとマークしたファイルです。
    """
    workspace_service = WorkspaceService(db)

    try:
        files = await workspace_service.get_presented_files(tenant_id, conversation_id)
        return PresentedFileList(
            conversation_id=conversation_id,
            files=files,
        )
    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Presentedファイル一覧取得エラー: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Presentedファイル一覧の取得に失敗しました",
        )
