"""
PDFファイル用ツール

inspect_pdf_file: 構造確認（ページ数、目次）
read_pdf_pages: テキスト抽出
convert_pdf_to_images: ページを画像化してワークスペースに保存
"""

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.services.workspace_service import WorkspaceService

logger = structlog.get_logger(__name__)


async def inspect_pdf_file_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    PDFファイルの構造を確認

    Args:
        args:
            file_path: ファイルパス
    """
    file_path = args.get("file_path", "")

    try:
        import pymupdf
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: PyMuPDFライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        doc = pymupdf.open(stream=content, filetype="pdf")

        result_lines = [
            f"# PDF構造: {filename}",
            f"ファイルサイズ: {len(content) / 1024:.1f} KB",
            f"ページ数: {len(doc)}",
            "",
        ]

        # メタデータ
        metadata = doc.metadata
        if metadata:
            result_lines.append("## メタデータ")
            if metadata.get("title"):
                result_lines.append(f"- タイトル: {metadata['title']}")
            if metadata.get("author"):
                result_lines.append(f"- 作成者: {metadata['author']}")
            if metadata.get("subject"):
                result_lines.append(f"- 件名: {metadata['subject']}")
            result_lines.append("")

        # 目次（アウトライン）
        toc = doc.get_toc()
        if toc:
            result_lines.append("## 目次")
            for level, title, page in toc[:30]:  # 最大30項目
                indent = "  " * (level - 1)
                result_lines.append(f"{indent}- {title} (p.{page})")
            if len(toc) > 30:
                result_lines.append(f"  ... 他 {len(toc) - 30} 項目")
            result_lines.append("")

        # 各ページの概要
        result_lines.append("## ページ概要")
        for i, page in enumerate(doc):
            if i >= 10:  # 最大10ページまで
                result_lines.append(f"... 他 {len(doc) - 10} ページ")
                break

            page_num = i + 1
            text = page.get_text()
            text_length = len(text)

            # ページの種類を推定
            images = page.get_images()
            page_type = "テキスト主体"
            if len(images) > 0 and text_length < 200:
                page_type = "図表主体"
            elif len(images) > 0:
                page_type = "テキスト+図表"

            # テキストのプレビュー
            preview = text[:100].replace("\n", " ").strip()
            if len(text) > 100:
                preview += "..."

            result_lines.append(f"\n### ページ {page_num}")
            result_lines.append(f"- 種類: {page_type}")
            result_lines.append(f"- 文字数: {text_length}")
            result_lines.append(f"- 画像数: {len(images)}")
            if preview:
                result_lines.append(f"- プレビュー: {preview}")

        doc.close()

        result_text = "\n".join(result_lines)
        result_text += "\n\n---\n"
        result_text += "テキストを取得するには `read_pdf_pages` を使用してください。\n"
        result_text += "図表を確認するには `convert_pdf_to_images` で画像化してください。"

        return {
            "content": [{"type": "text", "text": result_text}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("PDF構造確認エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


async def read_pdf_pages_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    PDFページのテキストを抽出

    Args:
        args:
            file_path: ファイルパス
            pages: ページ指定（例: "1-5" または "1,3,5"）
    """
    file_path = args.get("file_path", "")
    pages_spec = args.get("pages", "1-10")

    try:
        import pymupdf
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: PyMuPDFライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        doc = pymupdf.open(stream=content, filetype="pdf")
        total_pages = len(doc)

        # ページ番号を解析
        page_numbers = _parse_pages(pages_spec, total_pages)

        result_lines = [
            f"# {filename} のテキスト",
            f"取得ページ: {pages_spec} (全{total_pages}ページ中)",
            "",
        ]

        for page_num in page_numbers:
            if page_num < 1 or page_num > total_pages:
                continue

            page = doc[page_num - 1]  # 0-indexed
            text = page.get_text()

            result_lines.append(f"## ページ {page_num}")
            result_lines.append("")

            if text.strip():
                result_lines.append(text.strip())
            else:
                # テキストがない場合
                images = page.get_images()
                if images:
                    result_lines.append("[このページは図表主体です。テキストは抽出できませんでした。]")
                    result_lines.append(f"[画像数: {len(images)}]")
                    result_lines.append("[内容を確認するには `convert_pdf_to_images` で画像化してください。]")
                else:
                    result_lines.append("[このページにテキストは含まれていません。]")

            result_lines.append("")
            result_lines.append("---")
            result_lines.append("")

        doc.close()

        result_text = "\n".join(result_lines)

        return {
            "content": [{"type": "text", "text": result_text}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("PDFテキスト抽出エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


async def convert_pdf_to_images_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    PDFページを画像に変換してワークスペースに保存

    Args:
        args:
            file_path: ファイルパス
            pages: ページ指定（例: "1-3" または "1,3,5"）最大5ページ
            dpi: 解像度（デフォルト: 150）
    """
    file_path = args.get("file_path", "")
    pages_spec = args.get("pages", "1")
    dpi = args.get("dpi", 150)

    try:
        import pymupdf
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: PyMuPDFライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        doc = pymupdf.open(stream=content, filetype="pdf")
        total_pages = len(doc)

        # ページ番号を解析
        page_numbers = _parse_pages(pages_spec, total_pages)

        # 最大5ページに制限
        if len(page_numbers) > 5:
            page_numbers = page_numbers[:5]
            logger.warning("画像変換を5ページに制限", requested=pages_spec)

        # ベースファイル名
        base_name = Path(filename).stem

        saved_paths = []
        zoom = dpi / 72  # 72 DPIが標準

        for page_num in page_numbers:
            if page_num < 1 or page_num > total_pages:
                continue

            page = doc[page_num - 1]

            # 画像に変換
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            # PNG形式でバイトに変換
            img_bytes = pix.tobytes("png")

            # 保存パス
            output_path = f"generated/{base_name}_page_{page_num}.png"

            # ワークスペースに保存（S3にアップロード）
            await workspace_service.s3.upload(
                tenant_id,
                conversation_id,
                output_path,
                img_bytes,
                "image/png",
            )

            # DBに登録
            await workspace_service.register_ai_file(
                tenant_id,
                conversation_id,
                output_path,
                is_presented=False,
            )

            saved_paths.append(output_path)
            logger.info("PDF→画像変換完了", page=page_num, path=output_path)

        doc.close()

        result_lines = [
            f"# PDF画像変換完了",
            f"ファイル: {filename}",
            f"変換ページ: {len(saved_paths)}ページ",
            "",
            "## 保存された画像",
        ]

        for path in saved_paths:
            result_lines.append(f"- {path}")

        result_lines.append("")
        result_lines.append("---")
        result_lines.append("画像を確認するには `read_image_file` でパスを指定してください。")

        return {
            "content": [{"type": "text", "text": "\n".join(result_lines)}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("PDF画像変換エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"変換エラー: {str(e)}"}],
            "is_error": True,
        }


def _parse_pages(pages_spec: str, total_pages: int) -> list[int]:
    """
    ページ指定を解析

    Args:
        pages_spec: "1-5" または "1,3,5" 形式
        total_pages: 総ページ数

    Returns:
        ページ番号のリスト（1始まり）
    """
    page_numbers = []

    # カンマ区切り
    parts = pages_spec.replace(" ", "").split(",")

    for part in parts:
        if "-" in part:
            # 範囲指定 (1-5)
            try:
                start, end = part.split("-")
                start_num = int(start)
                end_num = int(end)
                page_numbers.extend(range(start_num, min(end_num + 1, total_pages + 1)))
            except ValueError:
                continue
        else:
            # 単一ページ
            try:
                page_numbers.append(int(part))
            except ValueError:
                continue

    return sorted(set(page_numbers))
