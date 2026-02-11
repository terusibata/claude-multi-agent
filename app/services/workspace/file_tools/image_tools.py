"""
画像ファイル用ツール

inspect_image_file: メタデータ確認（解像度、サイズ）
※ read_image_file は registry.py に実装
"""

import io
from typing import Any

import structlog

from app.services.workspace.file_processors import FileCategory, FileTypeClassifier
from app.services.workspace.file_tools.utils import (
    file_tool_handler,
    format_tool_error,
    format_tool_success,
)

logger = structlog.get_logger(__name__)


@file_tool_handler(log_prefix="画像情報取得")
async def inspect_image_file_handler(*, content, filename, content_type, args, **_):
    """
    画像ファイルのメタデータを確認

    Args:
        args:
            file_path: ファイルパス
    """
    # 画像ファイルかチェック
    category = FileTypeClassifier.get_category(filename, content_type)
    if category != FileCategory.IMAGE:
        return format_tool_error(
            f"このファイルは画像ではありません: {filename} ({content_type})"
        )

    result_lines = [
        f"# 画像情報: {filename}",
        f"ファイルサイズ: {len(content) / 1024:.1f} KB",
        f"MIMEタイプ: {content_type}",
    ]

    # Pillowで詳細情報を取得
    try:
        # オプション依存: 未インストール時はフォールバック
        from PIL import Image
        from PIL.ExifTags import TAGS

        img = Image.open(io.BytesIO(content))

        result_lines.append(f"解像度: {img.width} x {img.height} px")
        result_lines.append(f"カラーモード: {img.mode}")
        result_lines.append(f"フォーマット: {img.format}")

        # DPI情報
        dpi = img.info.get("dpi")
        if dpi:
            result_lines.append(f"DPI: {dpi[0]} x {dpi[1]}")

        # アニメーション（GIF）
        if hasattr(img, "n_frames") and img.n_frames > 1:
            result_lines.append(f"フレーム数: {img.n_frames}")

        # EXIF情報（簡易版）
        if hasattr(img, "_getexif") and img._getexif():
            exif = img._getexif()
            result_lines.append("")
            result_lines.append("## EXIF情報")

            important_tags = {
                "Make": "カメラメーカー",
                "Model": "カメラ機種",
                "DateTime": "撮影日時",
                "ExposureTime": "シャッタースピード",
                "FNumber": "絞り値",
                "ISOSpeedRatings": "ISO感度",
            }

            for tag_id, value in exif.items():
                tag_name = TAGS.get(tag_id, str(tag_id))
                if tag_name in important_tags:
                    result_lines.append(f"- {important_tags[tag_name]}: {value}")

        img.close()

    except ImportError:
        result_lines.append("")
        result_lines.append("※ Pillowがインストールされていないため、詳細情報を取得できません。")
    except Exception as e:
        logger.warning("画像詳細情報取得エラー", error=str(e))
        result_lines.append("")
        result_lines.append(f"※ 詳細情報の取得に失敗: {str(e)}")

    result_text = "\n".join(result_lines)
    result_text += "\n\n---\n"
    result_text += "画像を視覚的に確認するには `read_image_file` を使用してください。"

    return format_tool_success(result_text)
