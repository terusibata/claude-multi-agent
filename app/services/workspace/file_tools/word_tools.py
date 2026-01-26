"""
Wordファイル用ツール

inspect_word_file: 構造確認（見出し一覧）
read_word_section: セクション取得
"""

import io
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.services.workspace_service import WorkspaceService

logger = structlog.get_logger(__name__)


async def inspect_word_file_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    Wordファイルの構造を確認

    Args:
        args:
            file_path: ファイルパス
    """
    file_path = args.get("file_path", "")

    try:
        from docx import Document
        from docx.opc.exceptions import PackageNotFoundError
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: python-docxライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        doc = Document(io.BytesIO(content))

        result_lines = [
            f"# Word構造: {filename}",
            f"ファイルサイズ: {len(content) / 1024:.1f} KB",
            "",
        ]

        # 見出し構造を抽出
        headings = []
        paragraphs_count = 0
        tables_count = len(doc.tables)
        total_chars = 0

        for para in doc.paragraphs:
            paragraphs_count += 1
            total_chars += len(para.text)

            # 見出しスタイルを検出
            style_name = para.style.name if para.style else ""
            if style_name.startswith("Heading"):
                try:
                    level = int(style_name.replace("Heading ", "").replace("Heading", "1"))
                except ValueError:
                    level = 1
                headings.append({
                    "level": level,
                    "text": para.text.strip(),
                    "para_index": paragraphs_count,
                })

        result_lines.append(f"段落数: {paragraphs_count}")
        result_lines.append(f"表の数: {tables_count}")
        result_lines.append(f"総文字数: {total_chars}")
        result_lines.append("")

        # 見出し一覧
        if headings:
            result_lines.append("## 見出し構造")
            for h in headings[:50]:  # 最大50件
                indent = "  " * (h["level"] - 1)
                result_lines.append(f"{indent}- {h['text']} (段落{h['para_index']})")
            if len(headings) > 50:
                result_lines.append(f"... 他 {len(headings) - 50} 見出し")
            result_lines.append("")
        else:
            result_lines.append("## 見出し構造")
            result_lines.append("見出しは定義されていません。")
            result_lines.append("")

        # 最初の段落のプレビュー
        result_lines.append("## 冒頭プレビュー")
        preview_chars = 0
        for para in doc.paragraphs:
            if para.text.strip():
                result_lines.append(para.text.strip()[:200])
                preview_chars += len(para.text)
                if preview_chars > 500:
                    result_lines.append("...")
                    break

        result_text = "\n".join(result_lines)
        result_text += "\n\n---\n"
        result_text += "詳細を取得するには `read_word_section` を使用してください。"

        return {
            "content": [{"type": "text", "text": result_text}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("Word構造確認エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


async def read_word_section_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    Wordセクションのテキストを取得

    Args:
        args:
            file_path: ファイルパス
            heading: 見出しテキスト（部分一致）
            start_paragraph: 開始段落番号（headingが指定されていない場合）
            end_paragraph: 終了段落番号（デフォルト: start + 50）
    """
    file_path = args.get("file_path", "")
    heading = args.get("heading")
    start_paragraph = args.get("start_paragraph", 1)
    end_paragraph = args.get("end_paragraph")

    try:
        from docx import Document
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: python-docxライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        doc = Document(io.BytesIO(content))
        paragraphs = list(doc.paragraphs)
        total_paragraphs = len(paragraphs)

        result_lines = [f"# {filename} のテキスト", ""]

        if heading:
            # 見出しで検索
            found = False
            start_idx = 0
            end_idx = total_paragraphs

            for i, para in enumerate(paragraphs):
                style_name = para.style.name if para.style else ""
                if style_name.startswith("Heading") and heading.lower() in para.text.lower():
                    found = True
                    start_idx = i
                    current_level = int(style_name.replace("Heading ", "").replace("Heading", "1"))

                    # 次の同レベル以上の見出しまで
                    for j in range(i + 1, total_paragraphs):
                        next_style = paragraphs[j].style.name if paragraphs[j].style else ""
                        if next_style.startswith("Heading"):
                            try:
                                next_level = int(next_style.replace("Heading ", "").replace("Heading", "1"))
                                if next_level <= current_level:
                                    end_idx = j
                                    break
                            except ValueError:
                                pass
                    break

            if not found:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"見出し '{heading}' が見つかりませんでした。\n"
                                "`inspect_word_file` で見出し一覧を確認してください。",
                    }],
                    "is_error": True,
                }

            result_lines.append(f"## セクション: {heading}")
            result_lines.append(f"段落範囲: {start_idx + 1}-{end_idx}")
            result_lines.append("")

            for i in range(start_idx, min(end_idx, start_idx + 100)):  # 最大100段落
                para = paragraphs[i]
                if para.text.strip():
                    result_lines.append(para.text.strip())
                    result_lines.append("")

            if end_idx - start_idx > 100:
                result_lines.append(f"... 残り {end_idx - start_idx - 100} 段落")

        else:
            # 段落番号で取得
            if end_paragraph is None:
                end_paragraph = start_paragraph + 50

            start_idx = max(0, start_paragraph - 1)
            end_idx = min(total_paragraphs, end_paragraph)

            result_lines.append(f"段落範囲: {start_paragraph}-{end_idx} (全{total_paragraphs}段落)")
            result_lines.append("")

            for i in range(start_idx, end_idx):
                para = paragraphs[i]
                if para.text.strip():
                    result_lines.append(para.text.strip())
                    result_lines.append("")

            remaining = total_paragraphs - end_idx
            if remaining > 0:
                result_lines.append(f"---\n残り {remaining} 段落あります。")
                result_lines.append(f"続きを取得するには `start_paragraph={end_idx + 1}` を指定してください。")

        # 表の処理
        if doc.tables:
            result_lines.append("")
            result_lines.append(f"※ このドキュメントには {len(doc.tables)} 個の表が含まれています。")

        return {
            "content": [{"type": "text", "text": "\n".join(result_lines)}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("Wordセクション取得エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }
