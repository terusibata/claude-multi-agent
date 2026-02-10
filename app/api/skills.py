"""
Agent Skills管理API
ファイルシステムベースのSkills管理
"""
import structlog

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_skill_or_404
from app.database import get_db
from app.models.agent_skill import AgentSkill
from app.schemas.skill import (
    SkillCreate,
    SkillFilesResponse,
    SkillResponse,
    SkillUpdate,
    SlashCommandListResponse,
)
from app.services.skill_service import SkillService
from app.utils.exceptions import AppError
from app.utils.error_handler import app_error_to_http_exception, raise_not_found

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("", response_model=list[SkillResponse], summary="Skills一覧取得")
async def get_skills(
    tenant_id: str,
    status: str | None = Query(None, description="ステータスフィルター"),
    db: AsyncSession = Depends(get_db),
):
    """
    テナントのAgent Skills一覧を取得します。
    """
    service = SkillService(db)
    return await service.get_all_by_tenant(tenant_id, status=status)


@router.get(
    "/slash-commands",
    response_model=SlashCommandListResponse,
    summary="スラッシュコマンド一覧取得",
)
async def get_slash_commands(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    ユーザーが選択可能なスラッシュコマンド一覧を取得します。

    フロントエンドのオートコンプリート機能で使用します。
    返却される`name`フィールドの値を`preferred_skills`パラメータに渡してください。
    """
    service = SkillService(db)
    items = await service.get_slash_commands(tenant_id)
    return SlashCommandListResponse(items=items)


@router.get("/{skill_id}", response_model=SkillResponse, summary="Skill詳細取得")
async def get_skill(
    skill: AgentSkill = Depends(get_skill_or_404),
):
    """
    指定したIDのSkillを取得します。
    """
    return skill


async def _read_upload_file_safely(file: UploadFile) -> tuple[str, str]:
    """
    アップロードファイルを安全に読み込む

    Args:
        file: アップロードファイル

    Returns:
        (ファイル名, ファイル内容) のタプル

    Raises:
        HTTPException: ファイル名がない場合やエンコーディングエラーの場合
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ファイル名が指定されていません",
        )

    try:
        content = await file.read()
        decoded_content = content.decode("utf-8")
        return file.filename, decoded_content
    except UnicodeDecodeError:
        logger.warning("ファイルエンコーディングエラー", filename=file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ファイル '{file.filename}' はUTF-8でエンコードされていません",
        )
    except OSError as e:
        logger.error("ファイル読み込みエラー", filename=file.filename, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ファイルの読み込みに失敗しました",
        )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Skillアップロード",
)
async def upload_skill(
    tenant_id: str,
    name: str = Form(..., description="Skill名"),
    display_title: str | None = Form(None, description="表示タイトル"),
    description: str | None = Form(None, description="説明"),
    skill_md: UploadFile = File(..., description="SKILL.mdファイル"),
    additional_files: list[UploadFile] | None = File(default=None, description="追加ファイル"),
    db: AsyncSession = Depends(get_db),
):
    """
    新しいSkillをアップロードします。

    - **name**: Skill名（ディレクトリ名として使用）
    - **skill_md**: SKILL.mdファイル（必須）
    - **additional_files**: 追加のリソースファイル
    """
    service = SkillService(db)

    # 重複チェック
    existing = await service.get_by_name(name, tenant_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{name}' は既に存在します",
        )

    # ファイル内容を読み込み
    files = {}

    # SKILL.mdを読み込み
    _, skill_md_content = await _read_upload_file_safely(skill_md)
    files["SKILL.md"] = skill_md_content

    # 追加ファイルを読み込み（空文字列やNoneをスキップ）
    if additional_files:
        for file in additional_files:
            # curlで空の-Fパラメータが渡された場合をスキップ
            if file and file.filename:
                filename, content = await _read_upload_file_safely(file)
                files[filename] = content

    try:
        skill_data = SkillCreate(
            name=name,
            display_title=display_title,
            description=description,
        )

        return await service.create(tenant_id, skill_data, files)
    except AppError as e:
        raise app_error_to_http_exception(e)


@router.put("/{skill_id}", response_model=SkillResponse, summary="Skillメタデータ更新")
async def update_skill(
    tenant_id: str,
    skill_id: str,
    skill_data: SkillUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Skillのメタデータを更新します。
    """
    service = SkillService(db)
    skill = await service.update(skill_id, tenant_id, skill_data)
    if not skill:
        raise_not_found("Skill", skill_id)
    return skill


@router.put("/{skill_id}/files", response_model=SkillResponse, summary="Skillファイル更新")
async def update_skill_files(
    tenant_id: str,
    skill_id: str,
    files: list[UploadFile] = File(..., description="更新するファイル"),
    db: AsyncSession = Depends(get_db),
):
    """
    Skillのファイルを更新します。バージョンが上がります。
    """
    service = SkillService(db)

    # ファイル内容を読み込み
    file_contents = {}
    for file in files:
        filename, content = await _read_upload_file_safely(file)
        file_contents[filename] = content

    try:
        skill = await service.update_files(skill_id, tenant_id, file_contents)
        if not skill:
            raise_not_found("Skill", skill_id)
        return skill
    except AppError as e:
        raise app_error_to_http_exception(e)


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Skill削除",
)
async def delete_skill(
    tenant_id: str,
    skill_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Skillを削除します（ファイルシステムからも削除）。
    """
    service = SkillService(db)
    deleted = await service.delete(skill_id, tenant_id)
    if not deleted:
        raise_not_found("Skill", skill_id)


@router.get("/{skill_id}/files", response_model=SkillFilesResponse, summary="Skillファイル一覧")
async def get_skill_files(
    tenant_id: str,
    skill: AgentSkill = Depends(get_skill_or_404),
    db: AsyncSession = Depends(get_db),
):
    """
    Skillのファイル一覧を取得します。
    """
    service = SkillService(db)
    files = await service.get_files(skill.skill_id, tenant_id)
    return SkillFilesResponse(
        skill_id=skill.skill_id,
        skill_name=skill.name,
        files=files or [],
    )


@router.get("/{skill_id}/files/{file_path:path}", summary="Skillファイル内容取得")
async def get_skill_file_content(
    tenant_id: str,
    skill_id: str,
    file_path: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Skillの特定ファイルの内容を取得します。
    """
    service = SkillService(db)
    try:
        content = await service.get_file_content(skill_id, tenant_id, file_path)
        if content is None:
            raise_not_found("ファイル", file_path)
        return {"content": content}
    except AppError as e:
        raise app_error_to_http_exception(e)
