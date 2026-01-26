"""
PowerPointファイル用ツール

inspect_pptx_file: 構造確認（スライド一覧）
read_pptx_slides: スライドテキスト取得
"""

import io
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.services.workspace_service import WorkspaceService

logger = structlog.get_logger(__name__)


async def inspect_pptx_file_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    PowerPointファイルの構造を確認

    Args:
        args:
            file_path: ファイルパス
    """
    file_path = args.get("file_path", "")

    # 古いOffice形式（.ppt）のチェック
    if file_path.lower().endswith(".ppt"):
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"エラー: '{file_path}' は古いPowerPoint形式（.ppt）です。\n\n"
                    "このツールは .pptx（Office Open XML）形式のみ対応しています。\n"
                    ".ppt（バイナリ形式）ファイルは python-pptx では読み取れません。\n\n"
                    "対処方法:\n"
                    "1. Microsoft PowerPoint で .pptx 形式に変換して再アップロード\n"
                    "2. LibreOffice Impress で .pptx 形式に変換して再アップロード\n"
                    "3. オンライン変換ツールを使用"
                ),
            }],
            "is_error": True,
        }

    try:
        from pptx import Presentation
        from pptx.util import Inches
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: python-pptxライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        prs = Presentation(io.BytesIO(content))

        result_lines = [
            f"# PowerPoint構造: {filename}",
            f"ファイルサイズ: {len(content) / 1024:.1f} KB",
            f"スライド数: {len(prs.slides)}",
            "",
            "## スライド一覧",
        ]

        for i, slide in enumerate(prs.slides, 1):
            # タイトルを取得
            title = ""
            for shape in slide.shapes:
                if shape.has_text_frame:
                    if hasattr(shape, "is_placeholder") and shape.placeholder_format:
                        # タイトルプレースホルダーを検出
                        if shape.placeholder_format.type == 1:  # TITLE
                            title = shape.text_frame.text.strip()
                            break
                    elif not title and shape.text_frame.text.strip():
                        # 最初のテキストをタイトル候補として
                        title = shape.text_frame.text.strip()[:50]

            if not title:
                title = "(タイトルなし)"

            # 要素数を数える
            text_count = 0
            image_count = 0
            table_count = 0
            chart_count = 0

            for shape in slide.shapes:
                if shape.has_text_frame:
                    text_count += 1
                if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                    image_count += 1
                if shape.has_table:
                    table_count += 1
                if shape.has_chart:
                    chart_count += 1

            elements = []
            if text_count > 0:
                elements.append(f"テキスト{text_count}")
            if image_count > 0:
                elements.append(f"画像{image_count}")
            if table_count > 0:
                elements.append(f"表{table_count}")
            if chart_count > 0:
                elements.append(f"グラフ{chart_count}")

            element_str = ", ".join(elements) if elements else "空"

            result_lines.append(f"\n### スライド {i}")
            result_lines.append(f"- タイトル: {title}")
            result_lines.append(f"- 要素: {element_str}")

            # ノートがある場合
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    result_lines.append(f"- ノート: あり ({len(notes_text)}文字)")

        result_text = "\n".join(result_lines)
        result_text += "\n\n---\n"
        result_text += "詳細を取得するには `read_pptx_slides` を使用してください。"

        return {
            "content": [{"type": "text", "text": result_text}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("PowerPoint構造確認エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


async def read_pptx_slides_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    PowerPointスライドのテキストを取得

    Args:
        args:
            file_path: ファイルパス
            slides: スライド指定（例: "1-5" または "1,3,5"）
            include_notes: ノートを含めるか（デフォルト: True）
    """
    file_path = args.get("file_path", "")
    slides_spec = args.get("slides", "1-10")
    include_notes = args.get("include_notes", True)

    # 古いOffice形式（.ppt）のチェック
    if file_path.lower().endswith(".ppt"):
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"エラー: '{file_path}' は古いPowerPoint形式（.ppt）です。\n\n"
                    "このツールは .pptx（Office Open XML）形式のみ対応しています。\n"
                    ".ppt（バイナリ形式）ファイルは python-pptx では読み取れません。\n\n"
                    "対処方法:\n"
                    "1. Microsoft PowerPoint で .pptx 形式に変換して再アップロード\n"
                    "2. LibreOffice Impress で .pptx 形式に変換して再アップロード\n"
                    "3. オンライン変換ツールを使用"
                ),
            }],
            "is_error": True,
        }

    try:
        from pptx import Presentation
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: python-pptxライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        prs = Presentation(io.BytesIO(content))
        total_slides = len(prs.slides)

        # スライド番号を解析
        slide_numbers = _parse_slides(slides_spec, total_slides)

        result_lines = [
            f"# {filename} のテキスト",
            f"取得スライド: {slides_spec} (全{total_slides}スライド)",
            "",
        ]

        for slide_num in slide_numbers:
            if slide_num < 1 or slide_num > total_slides:
                continue

            slide = prs.slides[slide_num - 1]

            result_lines.append(f"## スライド {slide_num}")
            result_lines.append("")

            # テキストを抽出
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text.strip()
                        if text:
                            texts.append(text)

                # 表のテキスト
                if shape.has_table:
                    table = shape.table
                    for row in table.rows:
                        row_texts = []
                        for cell in row.cells:
                            row_texts.append(cell.text.strip())
                        if any(row_texts):
                            texts.append("| " + " | ".join(row_texts) + " |")

            if texts:
                for text in texts:
                    result_lines.append(text)
            else:
                result_lines.append("[このスライドにテキストは含まれていません]")

            # ノート
            if include_notes and slide.has_notes_slide:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    result_lines.append("")
                    result_lines.append("### ノート")
                    result_lines.append(notes_text)

            result_lines.append("")
            result_lines.append("---")
            result_lines.append("")

        return {
            "content": [{"type": "text", "text": "\n".join(result_lines)}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("PowerPointスライド取得エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


def _parse_slides(slides_spec: str, total_slides: int) -> list[int]:
    """
    スライド指定を解析

    Args:
        slides_spec: "1-5" または "1,3,5" 形式
        total_slides: 総スライド数

    Returns:
        スライド番号のリスト（1始まり）
    """
    slide_numbers = []

    # カンマ区切り
    parts = slides_spec.replace(" ", "").split(",")

    for part in parts:
        if "-" in part:
            # 範囲指定 (1-5)
            try:
                start, end = part.split("-")
                start_num = int(start)
                end_num = int(end)
                slide_numbers.extend(range(start_num, min(end_num + 1, total_slides + 1)))
            except ValueError:
                continue
        else:
            # 単一スライド
            try:
                slide_numbers.append(int(part))
            except ValueError:
                continue

    return sorted(set(slide_numbers))
