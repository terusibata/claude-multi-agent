"""
Agent Skills管理API
ファイルシステムベースのSkills管理
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.skill import (
    SkillCreate,
    SkillFilesResponse,
    SkillResponse,
    SkillUpdate,
    SlashCommandListResponse,
)
from app.services.skill_service import SkillService

router = APIRouter()


@router.get("", response_model=list[SkillResponse], summary="Skills一覧取得")
async def get_skills(
    tenant_id: str,
    status: Optional[str] = Query(None, description="ステータスフィルター"),
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
    tenant_id: str,
    skill_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    指定したIDのSkillを取得します。
    """
    service = SkillService(db)
    skill = await service.get_by_id(skill_id, tenant_id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' が見つかりません",
        )
    return skill


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Skillアップロード",
)
async def upload_skill(
    tenant_id: str,
    name: str = Form(..., description="Skill名"),
    display_title: Optional[str] = Form(None, description="表示タイトル"),
    description: Optional[str] = Form(None, description="説明"),
    skill_md: UploadFile = File(..., description="SKILL.mdファイル"),
    additional_files: list[UploadFile] = File(default=[], description="追加ファイル"),
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
    skill_md_content = await skill_md.read()
    files["SKILL.md"] = skill_md_content.decode("utf-8")

    # 追加ファイルを読み込み
    for file in additional_files:
        content = await file.read()
        # ファイル名にサブディレクトリが含まれる場合も対応
        files[file.filename] = content.decode("utf-8")

    skill_data = SkillCreate(
        name=name,
        display_title=display_title,
        description=description,
    )

    return await service.create(tenant_id, skill_data, files)


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' が見つかりません",
        )
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
        content = await file.read()
        file_contents[file.filename] = content.decode("utf-8")

    skill = await service.update_files(skill_id, tenant_id, file_contents)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' が見つかりません",
        )
    return skill


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' が見つかりません",
        )


@router.get("/{skill_id}/files", response_model=SkillFilesResponse, summary="Skillファイル一覧")
async def get_skill_files(
    tenant_id: str,
    skill_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Skillのファイル一覧を取得します。
    """
    service = SkillService(db)
    skill = await service.get_by_id(skill_id, tenant_id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' が見つかりません",
        )

    files = await service.get_files(skill_id, tenant_id)
    return SkillFilesResponse(
        skill_id=skill_id,
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
    content = await service.get_file_content(skill_id, tenant_id, file_path)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ファイル '{file_path}' が見つかりません",
        )
    return {"content": content}
