"""
Agent Skills管理API
ファイルシステムベースのSkills管理
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
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
from app.utils.error_handler import raise_not_found
from app.utils.exceptions import PathTraversalError, ValidationError
from app.utils.security import validate_zip_archive

router = APIRouter()


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
    slash_command: str | None = Form(None, description="スラッシュコマンド名"),
    slash_command_description: str | None = Form(None, description="スラッシュコマンドの説明"),
    is_user_selectable: bool = Form(True, description="ユーザーがUIから選択可能かどうか"),
    skill_archive: UploadFile = File(..., description="Skillファイル一式（ZIPアーカイブ）"),
    db: AsyncSession = Depends(get_db),
):
    """
    ZIPアーカイブから新しいSkillをアップロードします。

    ZIPにはSKILL.mdファイルを必ず含めてください。
    ディレクトリ階層はそのまま保持されます。

    ```bash
    # 使用例
    cd my-skill/
    zip -r ../my-skill.zip .
    curl -F "name=my-skill" -F "skill_archive=@my-skill.zip" \\
         https://api.example.com/api/tenants/{tenant_id}/skills
    ```
    """
    service = SkillService(db)

    # 重複チェック
    existing = await service.get_by_name(name, tenant_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{name}' は既に存在します",
        )

    # ZIPファイルを読み込み・検証・展開
    try:
        zip_data = await skill_archive.read()
        files = validate_zip_archive(zip_data)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except PathTraversalError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不正なファイルパスが含まれています: {e}",
        )

    skill_data = SkillCreate(
        name=name,
        display_title=display_title,
        description=description,
        slash_command=slash_command,
        slash_command_description=slash_command_description,
        is_user_selectable=is_user_selectable,
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
        raise_not_found("Skill", skill_id)
    return skill


@router.put("/{skill_id}/files", response_model=SkillResponse, summary="Skillファイル更新")
async def update_skill_files(
    tenant_id: str,
    skill_id: str,
    skill_archive: UploadFile = File(..., description="更新ファイル一式（ZIPアーカイブ）"),
    db: AsyncSession = Depends(get_db),
):
    """
    SkillのファイルをZIPアーカイブで更新します。バージョンが上がります。

    ZIPに含まれるファイルで既存ファイルを上書きします。
    ZIPに含まれないファイルはそのまま残ります。
    """
    service = SkillService(db)

    try:
        zip_data = await skill_archive.read()
        file_contents = validate_zip_archive(zip_data, require_skill_md=False)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except PathTraversalError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不正なファイルパスが含まれています: {e}",
        )

    skill = await service.update_files(skill_id, tenant_id, file_contents)
    if not skill:
        raise_not_found("Skill", skill_id)
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
        raise_not_found("Skill", skill_id)


@router.get("/{skill_id}/archive", summary="Skillアーカイブダウンロード")
async def download_skill_archive(
    tenant_id: str,
    skill_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    SkillのファイルをZIPアーカイブとしてダウンロードします。
    ディレクトリ階層はそのまま保持されます。
    """
    service = SkillService(db)
    result = await service.get_archive(skill_id, tenant_id)
    if result is None:
        raise_not_found("Skill", skill_id)

    zip_data, skill_name = result
    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_name}.zip"',
        },
    )


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
    result = await service.get_file_content(skill_id, tenant_id, file_path)
    if result is None:
        raise_not_found("ファイル", file_path)
    return result
