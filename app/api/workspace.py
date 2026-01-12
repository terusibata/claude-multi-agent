"""
ワークスペースAPI
セッション専用ワークスペースのファイル管理
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.workspace import (
    CleanupRequest,
    CleanupResponse,
    MultiUploadResponse,
    PresentedFileList,
    PresentFileRequest,
    UploadResponse,
    WorkspaceFileList,
    WorkspaceInfo,
)
from app.services.workspace_service import WorkspaceSecurityError, WorkspaceService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/sessions/{session_id}/workspace",
    response_model=WorkspaceInfo,
    summary="ワークスペース作成",
)
async def create_workspace(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    セッション専用ワークスペースを作成します。

    ワークスペースはセッションごとに独立しており、
    他のセッションからはアクセスできません。
    """
    workspace_service = WorkspaceService(db)

    try:
        workspace_info = await workspace_service.create_workspace(tenant_id, session_id)
        await db.commit()
        return workspace_info
    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"ワークスペース作成エラー: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ワークスペースの作成に失敗しました",
        )


@router.get(
    "/sessions/{session_id}/workspace",
    response_model=WorkspaceInfo,
    summary="ワークスペース情報取得",
)
async def get_workspace_info(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    セッション専用ワークスペースの情報を取得します。
    """
    workspace_service = WorkspaceService(db)

    try:
        workspace_info = await workspace_service.get_workspace_info(tenant_id, session_id)
        if not workspace_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="ワークスペースが見つかりません",
            )
        return workspace_info
    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )


@router.post(
    "/sessions/{session_id}/upload-files",
    response_model=MultiUploadResponse,
    summary="ファイルアップロード",
)
async def upload_files(
    tenant_id: str,
    session_id: str,
    files: list[UploadFile] = File(..., description="アップロードするファイル"),
    target_dir: str = Form("uploads", description="保存先ディレクトリ（ワークスペース内）"),
    db: AsyncSession = Depends(get_db),
):
    """
    ファイルをセッション専用ワークスペースにアップロードします。

    ## 注意事項
    - 同じファイルパスに再アップロードすると、バージョンが自動的にインクリメントされます
    - ファイルサイズの上限は50MBです
    - ワークスペース全体の上限は500MBです
    """
    workspace_service = WorkspaceService(db)
    uploaded_files = []
    failed_files = []

    try:
        for file in files:
            try:
                # ファイル内容を読み込み
                content = await file.read()

                # ファイルパスを構築
                file_path = f"{target_dir}/{file.filename}"

                # アップロード実行
                file_info = await workspace_service.upload_file(
                    tenant_id=tenant_id,
                    chat_session_id=session_id,
                    file_path=file_path,
                    content=content,
                    original_name=file.filename,
                )
                uploaded_files.append(file_info)

            except WorkspaceSecurityError as e:
                failed_files.append({
                    "filename": file.filename,
                    "error": str(e),
                })
            except Exception as e:
                logger.error(f"ファイルアップロードエラー: {file.filename} - {e}")
                failed_files.append({
                    "filename": file.filename,
                    "error": "アップロードに失敗しました",
                })

        await db.commit()

        success = len(failed_files) == 0
        message = (
            f"{len(uploaded_files)}ファイルをアップロードしました"
            if success
            else f"{len(uploaded_files)}ファイルをアップロードしました（{len(failed_files)}ファイル失敗）"
        )

        return MultiUploadResponse(
            success=success,
            uploaded_files=uploaded_files,
            failed_files=failed_files,
            message=message,
        )

    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"ファイルアップロードエラー: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ファイルのアップロードに失敗しました",
        )


@router.get(
    "/sessions/{session_id}/list-files",
    response_model=WorkspaceFileList,
    summary="ファイル一覧取得",
)
async def list_files(
    tenant_id: str,
    session_id: str,
    include_all_versions: bool = Query(
        False,
        description="全バージョンを含めるか（デフォルト: 最新のみ）",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    セッション専用ワークスペース内のファイル一覧を取得します。

    ## レスポンス
    - デフォルトでは各ファイルの最新バージョンのみ返します
    - `include_all_versions=true` で全バージョンを取得できます
    """
    workspace_service = WorkspaceService(db)

    try:
        return await workspace_service.list_files(
            tenant_id,
            session_id,
            include_all_versions=include_all_versions,
        )
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
    "/sessions/{session_id}/download-file",
    summary="ファイルダウンロード",
)
async def download_file(
    tenant_id: str,
    session_id: str,
    path: str = Query(..., description="ファイルパス（ワークスペース内）"),
    version: Optional[int] = Query(None, description="バージョン番号（省略時は最新）"),
    db: AsyncSession = Depends(get_db),
):
    """
    ファイルをダウンロードします。

    ## パラメータ
    - `path`: ワークスペース内のファイルパス（例: `uploads/data.csv`）
    - `version`: 特定のバージョンを取得する場合に指定

    ## セキュリティ
    - ワークスペース外へのパストラバーサル攻撃は自動的にブロックされます
    """
    workspace_service = WorkspaceService(db)

    try:
        content, filename, mime_type = await workspace_service.download_file(
            tenant_id,
            session_id,
            path,
            version=version,
        )

        return Response(
            content=content,
            media_type=mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
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


@router.get(
    "/sessions/{session_id}/presented-files",
    response_model=PresentedFileList,
    summary="Presentedファイル一覧取得",
)
async def get_presented_files(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    AIがユーザーに提示したファイル（Presented files）の一覧を取得します。

    これらは、AIが作成してユーザーに返したいとマークしたファイルです。
    """
    workspace_service = WorkspaceService(db)

    try:
        files = await workspace_service.get_presented_files(tenant_id, session_id)
        return PresentedFileList(
            chat_session_id=session_id,
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


@router.post(
    "/sessions/{session_id}/present-file",
    summary="ファイルをPresentedとしてマーク",
)
async def present_file(
    tenant_id: str,
    session_id: str,
    request: PresentFileRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    ファイルをPresentedとしてマークします。

    Presentedファイルは、AIがユーザーに返したいファイルとして識別されます。
    """
    workspace_service = WorkspaceService(db)

    try:
        file_info = await workspace_service.set_presented(
            tenant_id,
            session_id,
            request.file_path,
            description=request.description,
        )

        if not file_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="ファイルが見つかりません",
            )

        await db.commit()
        return {"success": True, "file": file_info}

    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ファイルPresent設定エラー: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ファイルのPresent設定に失敗しました",
        )


@router.post(
    "/workspace/cleanup",
    response_model=CleanupResponse,
    summary="古いワークスペースのクリーンアップ",
)
async def cleanup_workspaces(
    tenant_id: str,
    request: CleanupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    古いワークスペースをクリーンアップします。

    ## パラメータ
    - `older_than_days`: この日数より古いアーカイブ済みセッションが対象
    - `dry_run`: `true`の場合、削除せずに対象のリストのみ返します

    ## 注意
    - アーカイブ済み（status="archived"）のセッションのみが対象です
    - アクティブなセッションは削除されません
    """
    workspace_service = WorkspaceService(db)

    try:
        result = await workspace_service.cleanup_old_workspaces(
            tenant_id,
            older_than_days=request.older_than_days,
            dry_run=request.dry_run,
        )

        if not request.dry_run:
            await db.commit()

        return CleanupResponse(**result)

    except WorkspaceSecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"ワークスペースクリーンアップエラー: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ワークスペースのクリーンアップに失敗しました",
        )
