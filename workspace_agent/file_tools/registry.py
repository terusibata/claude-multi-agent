"""
ファイルツールレジストリ（コンテナ側）

ツールハンドラーの登録と共通ハンドラー（ファイル一覧・画像読み込み）を提供。
各形式固有のツールは個別モジュール（excel_tools, word_tools 等）で実装。

ホスト側との違い:
- workspace_service の代わりにローカルファイルシステム（/workspace）を使用
- FileTypeClassifier の代わりにローカルのMIMEタイプ判定を使用
- ハンドラーは (args: dict) のみを受け取る
"""

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ワークスペースのルートディレクトリ
WORKSPACE_ROOT = Path("/workspace")


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


# =============================================================================
# ファイルカテゴリ判定（FileTypeClassifier の代替）
# =============================================================================

# MIMEタイプによるカテゴリ分類
_CATEGORY_MAP = {
    "image": {
        "image/jpeg", "image/jpg", "image/png", "image/gif",
        "image/webp", "image/bmp", "image/tiff", "image/svg+xml",
    },
    "pdf": {
        "application/pdf",
    },
    "office": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
    },
    "text": {
        "text/plain", "text/csv", "text/html", "text/css",
        "text/javascript", "application/json", "application/xml",
        "text/markdown", "text/x-python", "text/x-java",
    },
}

# 拡張子によるカテゴリ分類（フォールバック用）
_EXTENSION_CATEGORY_MAP = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg"},
    "pdf": {".pdf"},
    "office": {".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt"},
    "text": {
        ".txt", ".csv", ".html", ".css", ".js", ".json", ".xml",
        ".md", ".py", ".java", ".ts", ".tsx", ".jsx", ".yaml", ".yml",
        ".sh", ".bash", ".log", ".ini", ".cfg", ".conf", ".toml",
    },
}


def _get_file_category(file_path: str, mime_type: str | None) -> str:
    """ファイルのカテゴリを判定"""
    # MIMEタイプで判定
    if mime_type:
        for category, mime_types in _CATEGORY_MAP.items():
            if mime_type in mime_types:
                return category

    # 拡張子でフォールバック判定
    ext = Path(file_path).suffix.lower()
    for category, extensions in _EXTENSION_CATEGORY_MAP.items():
        if ext in extensions:
            return category

    return "other"


# =============================================================================
# 共通ハンドラー
# =============================================================================

async def list_workspace_files_handler(args: dict[str, Any]) -> dict[str, Any]:
    """
    ワークスペースファイル一覧を取得（ローカルファイルシステムから）

    Args:
        args:
            filter_type: フィルタタイプ (image/pdf/office/text/all)
    """
    filter_type = args.get("filter_type", "all")

    try:
        files_info = []

        for root, dirs, files in os.walk(WORKSPACE_ROOT):
            # 隠しディレクトリをスキップ
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for filename in files:
                # 隠しファイルをスキップ
                if filename.startswith('.'):
                    continue

                full_path = Path(root) / filename
                rel_path = full_path.relative_to(WORKSPACE_ROOT)
                file_path_str = str(rel_path)

                # ファイルサイズを取得
                try:
                    file_size = full_path.stat().st_size
                except OSError:
                    continue

                # MIMEタイプを推測
                mime_type, _ = mimetypes.guess_type(str(full_path))
                category = _get_file_category(file_path_str, mime_type)

                if filter_type != "all" and category != filter_type:
                    continue

                files_info.append({
                    "path": file_path_str,
                    "name": filename,
                    "size": file_size,
                    "type": category,
                    "mime_type": mime_type or "application/octet-stream",
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


async def read_image_file_handler(args: dict[str, Any]) -> dict[str, Any]:
    """
    画像ファイルを視覚的に読み込み（ローカルファイルシステムから）

    Args:
        args:
            file_path: ファイルパス
            max_dimension: 最大サイズ（デフォルト: 1920）
    """
    file_path = args.get("file_path", "")
    max_dimension = args.get("max_dimension", 1920)

    try:
        # ローカルファイルシステムから読み込み
        full_path = WORKSPACE_ROOT / file_path
        content = full_path.read_bytes()
        filename = full_path.name
        content_type, _ = mimetypes.guess_type(str(full_path))

        # サポートされている画像形式かチェック
        category = _get_file_category(filename, content_type)
        if category != "image":
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


# =============================================================================
# ハンドラー登録
# =============================================================================

def create_file_tools_handlers() -> dict[str, Any]:
    """
    ファイルツールハンドラーを作成（コンテナ側）

    ホスト側との違い:
    - workspace_service, tenant_id, conversation_id のバインドが不要
    - 各ハンドラーは直接 (args: dict) を受け取る

    Returns:
        ハンドラー辞書 {ツール名: ハンドラー関数}
    """
    # 使用時のみロードするため遅延インポート
    from workspace_agent.file_tools.excel_tools import (
        get_sheet_info_handler,
        get_sheet_csv_handler,
        search_workbook_handler,
    )
    from workspace_agent.file_tools.pdf_tools import (
        inspect_pdf_file_handler,
        read_pdf_pages_handler,
        convert_pdf_to_images_handler,
    )
    from workspace_agent.file_tools.word_tools import (
        get_document_info_handler,
        get_document_content_handler,
        search_document_handler,
    )
    from workspace_agent.file_tools.pptx_tools import (
        get_presentation_info_handler,
        get_slides_content_handler,
        search_presentation_handler,
    )
    from workspace_agent.file_tools.image_tools import (
        inspect_image_file_handler,
    )

    return {
        # 共通
        "list_workspace_files": list_workspace_files_handler,
        "read_image_file": read_image_file_handler,
        # Excel
        "get_sheet_info": get_sheet_info_handler,
        "get_sheet_csv": get_sheet_csv_handler,
        "search_workbook": search_workbook_handler,
        # PDF
        "inspect_pdf_file": inspect_pdf_file_handler,
        "read_pdf_pages": read_pdf_pages_handler,
        "convert_pdf_to_images": convert_pdf_to_images_handler,
        # Word
        "get_document_info": get_document_info_handler,
        "get_document_content": get_document_content_handler,
        "search_document": search_document_handler,
        # PowerPoint
        "get_presentation_info": get_presentation_info_handler,
        "get_slides_content": get_slides_content_handler,
        "search_presentation": search_presentation_handler,
        # 画像
        "inspect_image_file": inspect_image_file_handler,
    }
