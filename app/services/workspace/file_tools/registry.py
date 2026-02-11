"""
ファイルツールレジストリ

ツールハンドラーの登録と共通ハンドラー（ファイル一覧・画像読み込み）を提供。
各形式固有のツールは個別モジュール（excel_tools, word_tools 等）で実装。
"""

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from app.services.workspace.file_processors import FileCategory, FileTypeClassifier

if TYPE_CHECKING:
    from app.services.workspace_service import WorkspaceService

logger = structlog.get_logger(__name__)


# システムプロンプト
FILE_TOOLS_PROMPT = """
## ファイル読み込み

ワークスペースのファイルは以下の手順で読んでください：
1. list_workspace_files でファイル一覧を確認
2. 構造確認
   - Excel: get_sheet_info
   - PDF: inspect_pdf_file
   - Word: get_document_info
   - PowerPoint: get_presentation_info
   - 画像: inspect_image_file
3. データ取得
   - Excel: get_sheet_csv
   - PDF: read_pdf_pages
   - Word: get_document_content
   - PowerPoint: get_slides_content
4. 検索
   - Excel: search_workbook
   - Word: search_document
   - PowerPoint: search_presentation
5. 図表など視覚的確認が必要な場合のみ convert_pdf_to_images → read_image_file

※ 画像読み込みはコンテキストを消費するため、必要な場合のみ使用
※ テキスト/CSV/JSONファイルは従来のReadツールも使用可能
"""


async def list_workspace_files_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    ワークスペースファイル一覧を取得

    Args:
        args:
            filter_type: フィルタタイプ (image/pdf/office/text/all)
    """
    filter_type = args.get("filter_type", "all")

    try:
        file_list = await workspace_service.list_files(tenant_id, conversation_id)

        files_info = []
        for f in file_list.files:
            category = FileTypeClassifier.get_category(f.file_path, f.mime_type)
            category_str = category.value

            if filter_type != "all" and category_str != filter_type:
                continue

            files_info.append({
                "path": f.file_path,
                "name": f.original_name,
                "size": f.file_size,
                "type": category_str,
                "mime_type": f.mime_type,
            })

        # テキスト形式で返却
        result_text = f"ワークスペース内のファイル一覧（{len(files_info)}件）:\n\n"
        for f in files_info:
            size_kb = f["size"] / 1024
            result_text += f"- {f['path']} ({f['type']}, {size_kb:.1f}KB)\n"

        if not files_info:
            result_text = "ワークスペースにファイルがありません。"

        return {
            "content": [{"type": "text", "text": result_text}],
        }
    except Exception as e:
        logger.error("ファイル一覧取得エラー", error=str(e))
        return {
            "content": [{"type": "text", "text": f"エラー: {str(e)}"}],
            "is_error": True,
        }


async def read_image_file_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    画像ファイルを視覚的に読み込み

    Args:
        args:
            file_path: ファイルパス
            max_dimension: 最大サイズ（デフォルト: 1920）
    """
    file_path = args.get("file_path", "")
    max_dimension = args.get("max_dimension", 1920)

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        # サポートされている画像形式かチェック
        category = FileTypeClassifier.get_category(filename, content_type)
        if category != FileCategory.IMAGE:
            return {
                "content": [{
                    "type": "text",
                    "text": f"このファイルは画像ではありません: {filename} ({content_type})\n"
                            "画像ファイル（JPEG/PNG/GIF/WebP）を指定してください。",
                }],
                "is_error": True,
            }

        # 画像リサイズ（必要な場合）
        resized_content, final_content_type = await _resize_image_if_needed(
            content, content_type, max_dimension
        )

        # Base64エンコード
        base64_data = base64.b64encode(resized_content).decode("utf-8")

        # image content blockで返す
        return {
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": final_content_type,
                        "data": base64_data,
                    },
                }
            ],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("画像ファイル読み込みエラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


async def _resize_image_if_needed(
    content: bytes,
    content_type: str,
    max_dimension: int,
) -> tuple[bytes, str]:
    """
    必要に応じて画像をリサイズ

    Returns:
        (リサイズ後のcontent, content_type)
    """
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(content))
        width, height = img.size

        # リサイズが必要か判定
        if width <= max_dimension and height <= max_dimension:
            return content, content_type

        # アスペクト比を維持してリサイズ
        if width > height:
            new_width = max_dimension
            new_height = int(height * (max_dimension / width))
        else:
            new_height = max_dimension
            new_width = int(width * (max_dimension / height))

        img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # 出力形式を決定
        output_format = "PNG"
        output_content_type = "image/png"
        if content_type in ["image/jpeg", "image/jpg"]:
            output_format = "JPEG"
            output_content_type = "image/jpeg"
            # JPEGはRGBAをサポートしないので変換
            if img_resized.mode == "RGBA":
                img_resized = img_resized.convert("RGB")

        # バイトに変換
        output = io.BytesIO()
        img_resized.save(output, format=output_format, quality=85)
        return output.getvalue(), output_content_type

    except ImportError:
        logger.warning("Pillowがインストールされていないため、リサイズをスキップ")
        return content, content_type
    except Exception as e:
        logger.warning("画像リサイズに失敗", error=str(e))
        return content, content_type


def create_file_tools_handlers(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
):
    """
    ファイルツールハンドラーを作成

    Returns:
        ハンドラー辞書
    """
    # 使用時のみロードするため遅延インポート
    from app.services.workspace.file_tools.excel_tools import (
        get_sheet_info_handler,
        get_sheet_csv_handler,
        search_workbook_handler,
    )
    from app.services.workspace.file_tools.pdf_tools import (
        inspect_pdf_file_handler,
        read_pdf_pages_handler,
        convert_pdf_to_images_handler,
    )
    from app.services.workspace.file_tools.word_tools import (
        get_document_info_handler,
        get_document_content_handler,
        search_document_handler,
    )
    from app.services.workspace.file_tools.pptx_tools import (
        get_presentation_info_handler,
        get_slides_content_handler,
        search_presentation_handler,
    )
    from app.services.workspace.file_tools.image_tools import (
        inspect_image_file_handler,
    )

    # 共通引数をバインド
    def bind_handler(handler):
        async def bound_handler(args: dict[str, Any]) -> dict[str, Any]:
            return await handler(workspace_service, tenant_id, conversation_id, args)
        return bound_handler

    return {
        # 共通
        "list_workspace_files": bind_handler(list_workspace_files_handler),
        "read_image_file": bind_handler(read_image_file_handler),
        # Excel
        "get_sheet_info": bind_handler(get_sheet_info_handler),
        "get_sheet_csv": bind_handler(get_sheet_csv_handler),
        "search_workbook": bind_handler(search_workbook_handler),
        # PDF
        "inspect_pdf_file": bind_handler(inspect_pdf_file_handler),
        "read_pdf_pages": bind_handler(read_pdf_pages_handler),
        "convert_pdf_to_images": bind_handler(convert_pdf_to_images_handler),
        # Word
        "get_document_info": bind_handler(get_document_info_handler),
        "get_document_content": bind_handler(get_document_content_handler),
        "search_document": bind_handler(search_document_handler),
        # PowerPoint
        "get_presentation_info": bind_handler(get_presentation_info_handler),
        "get_slides_content": bind_handler(get_slides_content_handler),
        "search_presentation": bind_handler(search_presentation_handler),
        # 画像
        "inspect_image_file": bind_handler(inspect_image_file_handler),
    }
