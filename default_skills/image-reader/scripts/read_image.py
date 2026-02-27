#!/usr/bin/env python3
"""
Image Reader - 画像メタデータ解析スクリプト (AI最適化版)

機能:
  - 画像基本情報（サイズ、解像度、カラーモード、ファイルサイズ）
  - EXIF情報（カメラ、撮影条件、GPS等）
  - AI向けコンパクト出力 / JSON出力 / 人間向けテキスト出力

使用例:
  python3 read_image.py photo.jpg                        # AI向け出力(デフォルト)
  python3 read_image.py photo.jpg --exif                  # 詳細EXIF情報
  python3 read_image.py photo.jpg --format human           # 人間向け出力
  python3 read_image.py photo.jpg --format json             # JSON出力(空値省略)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def ensure_pillow() -> None:
    """Pillow がなければインストールする。"""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow をインストール中...", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "Pillow",
             "--break-system-packages", "-q"],
        )


ensure_pillow()

from PIL import Image, ExifTags  # noqa: E402


# ---------------------------------------------------------------------------
# EXIF タグマッピング
# ---------------------------------------------------------------------------

# 主要EXIFタグの日本語名
EXIF_TAG_LABELS: dict[str, str] = {
    "Make": "メーカー",
    "Model": "カメラ",
    "DateTime": "撮影日時",
    "DateTimeOriginal": "撮影日時",
    "DateTimeDigitized": "デジタル化日時",
    "ExposureTime": "シャッター速度",
    "FNumber": "F値",
    "ISOSpeedRatings": "ISO",
    "FocalLength": "焦点距離",
    "FocalLengthIn35mmFilm": "35mm換算焦点距離",
    "ExposureBiasValue": "露出補正",
    "MeteringMode": "測光モード",
    "Flash": "フラッシュ",
    "WhiteBalance": "ホワイトバランス",
    "ImageWidth": "画像幅",
    "ImageLength": "画像高さ",
    "Orientation": "回転情報",
    "Software": "ソフトウェア",
    "LensModel": "レンズ",
    "LensMake": "レンズメーカー",
}

METERING_MODES: dict[int, str] = {
    0: "不明", 1: "平均", 2: "中央重点", 3: "スポット",
    4: "マルチスポット", 5: "パターン", 6: "部分",
}


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class BasicInfo:
    filename: str = ""
    width: int = 0
    height: int = 0
    format: str = ""
    mode: str = ""
    dpi: Optional[tuple[int, int]] = None
    file_size_bytes: int = 0
    has_alpha: bool = False
    frames: int = 1


@dataclass
class ExifInfo:
    camera: str = ""
    maker: str = ""
    lens: str = ""
    datetime: str = ""
    exposure_time: str = ""
    f_number: str = ""
    iso: str = ""
    focal_length: str = ""
    focal_length_35mm: str = ""
    exposure_bias: str = ""
    metering_mode: str = ""
    flash: str = ""
    white_balance: str = ""
    orientation: int = 0
    software: str = ""
    gps_latitude: str = ""
    gps_longitude: str = ""
    raw_tags: dict[str, str] = field(default_factory=dict)


@dataclass
class ImageInfo:
    basic: BasicInfo = field(default_factory=BasicInfo)
    exif: Optional[ExifInfo] = None


# ---------------------------------------------------------------------------
# パーサー
# ---------------------------------------------------------------------------

def _format_exposure(val) -> str:
    """露出時間をわかりやすい形式に変換する。"""
    if hasattr(val, "numerator") and hasattr(val, "denominator"):
        if val.numerator == 0:
            return "0"
        if val.denominator == 0:
            return str(val)
        ratio = val.numerator / val.denominator
        if ratio >= 1:
            return f"{ratio:.1f}s"
        return f"1/{int(val.denominator / val.numerator)}s"
    return str(val)


def _format_fnumber(val) -> str:
    if hasattr(val, "numerator") and hasattr(val, "denominator"):
        if val.denominator == 0:
            return str(val)
        return f"F{val.numerator / val.denominator:.1f}"
    return f"F{val}"


def _format_focal_length(val) -> str:
    if hasattr(val, "numerator") and hasattr(val, "denominator"):
        if val.denominator == 0:
            return str(val)
        return f"{val.numerator / val.denominator:.0f}mm"
    return f"{val}mm"


def _format_gps_coord(gps_data: dict, ref_key: str, coord_key: str) -> str:
    """GPS座標を度分秒形式に変換する。"""
    ref = gps_data.get(ref_key, "")
    coord = gps_data.get(coord_key)
    if not coord or len(coord) < 3:
        return ""
    try:
        degrees = float(coord[0])
        minutes = float(coord[1])
        seconds = float(coord[2])
        decimal = degrees + minutes / 60 + seconds / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return f"{decimal:.6f}"
    except (ValueError, TypeError, IndexError):
        return ""


def parse_image(filepath: str) -> ImageInfo:
    path = Path(filepath)
    img = Image.open(filepath)

    basic = BasicInfo(
        filename=path.name,
        width=img.width,
        height=img.height,
        format=img.format or path.suffix[1:].upper(),
        mode=img.mode,
        file_size_bytes=path.stat().st_size,
        has_alpha="A" in (img.mode or ""),
    )

    # DPI
    dpi = img.info.get("dpi")
    if dpi:
        basic.dpi = (int(dpi[0]), int(dpi[1]))

    # Frames (for animated GIF/WebP)
    try:
        basic.frames = getattr(img, "n_frames", 1)
    except Exception:
        basic.frames = 1

    info = ImageInfo(basic=basic)

    # EXIF
    try:
        exif_data = img.getexif()
        if exif_data:
            exif = ExifInfo()
            raw_tags: dict[str, str] = {}

            # IFD0 (メイン) + Exif サブIFD のタグを統合して処理
            all_tags: dict[int, object] = dict(exif_data.items())
            exif_ifd = exif_data.get_ifd(ExifTags.IFD.Exif)
            if exif_ifd:
                all_tags.update(exif_ifd)

            for tag_id, value in all_tags.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))

                # Store raw for --exif mode
                try:
                    raw_tags[tag_name] = str(value)[:200]
                except Exception:
                    pass

                if tag_name == "Make":
                    exif.maker = str(value).strip()
                elif tag_name == "Model":
                    exif.camera = str(value).strip()
                elif tag_name in ("DateTimeOriginal", "DateTime"):
                    if not exif.datetime:
                        exif.datetime = str(value).strip()
                elif tag_name == "ExposureTime":
                    exif.exposure_time = _format_exposure(value)
                elif tag_name == "FNumber":
                    exif.f_number = _format_fnumber(value)
                elif tag_name == "ISOSpeedRatings":
                    exif.iso = str(value)
                elif tag_name == "FocalLength":
                    exif.focal_length = _format_focal_length(value)
                elif tag_name == "FocalLengthIn35mmFilm":
                    exif.focal_length_35mm = f"{value}mm"
                elif tag_name == "ExposureBiasValue":
                    if hasattr(value, "numerator"):
                        bias = value.numerator / value.denominator if value.denominator else 0
                        exif.exposure_bias = f"{bias:+.1f}EV"
                    else:
                        exif.exposure_bias = str(value)
                elif tag_name == "MeteringMode":
                    exif.metering_mode = METERING_MODES.get(value, str(value))
                elif tag_name == "Flash":
                    exif.flash = "発光" if value & 1 else "非発光"
                elif tag_name == "WhiteBalance":
                    exif.white_balance = "自動" if value == 0 else "手動"
                elif tag_name == "Orientation":
                    exif.orientation = value
                elif tag_name == "Software":
                    exif.software = str(value).strip()
                elif tag_name == "LensModel":
                    exif.lens = str(value).strip()

            # GPS サブIFD を個別に処理
            gps_ifd = exif_data.get_ifd(ExifTags.IFD.GPSInfo)
            if gps_ifd:
                gps_data: dict[str, object] = {}
                for gps_tag_id, gps_value in gps_ifd.items():
                    gps_tag = ExifTags.GPSTAGS.get(gps_tag_id, str(gps_tag_id))
                    gps_data[gps_tag] = gps_value
                exif.gps_latitude = _format_gps_coord(gps_data, "GPSLatitudeRef", "GPSLatitude")
                exif.gps_longitude = _format_gps_coord(gps_data, "GPSLongitudeRef", "GPSLongitude")

            exif.raw_tags = raw_tags
            info.exif = exif
    except (AttributeError, Exception):
        pass

    img.close()
    return info


# ---------------------------------------------------------------------------
# AI向けコンパクト出力
# ---------------------------------------------------------------------------

def format_ai(info: ImageInfo, show_exif_detail: bool = False) -> str:
    lines: list[str] = []
    b = info.basic

    # Basic info (1 line)
    parts: list[str] = [
        b.filename,
        f"{b.width}x{b.height}",
        b.format,
        b.mode,
    ]
    if b.dpi:
        parts.append(f"{b.dpi[0]}DPI")
    size_kb = b.file_size_bytes / 1024
    if size_kb > 1024:
        parts.append(f"{size_kb / 1024:.1f}MB")
    else:
        parts.append(f"{size_kb:.1f}KB")
    if b.has_alpha:
        parts.append("アルファ有")
    if b.frames > 1:
        parts.append(f"{b.frames}フレーム")
    lines.append(f"[IMG] {' | '.join(parts)}")

    # EXIF summary (1 line)
    if info.exif:
        e = info.exif
        exif_parts: list[str] = []
        if e.camera:
            exif_parts.append(f"カメラ:{e.camera}")
        elif e.maker:
            exif_parts.append(f"メーカー:{e.maker}")
        if e.lens:
            exif_parts.append(f"レンズ:{e.lens}")
        if e.f_number:
            exif_parts.append(e.f_number)
        if e.exposure_time:
            exif_parts.append(e.exposure_time)
        if e.iso:
            exif_parts.append(f"ISO{e.iso}")
        if e.focal_length:
            exif_parts.append(e.focal_length)
        if e.datetime:
            exif_parts.append(e.datetime)
        if exif_parts:
            lines.append(f"[EXIF] {' | '.join(exif_parts)}")

        if e.gps_latitude and e.gps_longitude:
            lines.append(f"[GPS] {e.gps_latitude}, {e.gps_longitude}")

        # Detailed EXIF
        if show_exif_detail and e.raw_tags:
            lines.append("")
            lines.append("[EXIF詳細]")
            for tag_name, tag_value in sorted(e.raw_tags.items()):
                label = EXIF_TAG_LABELS.get(tag_name, tag_name)
                lines.append(f"  {label}: {tag_value}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 人間向け出力
# ---------------------------------------------------------------------------

def format_human(info: ImageInfo, show_exif_detail: bool = False) -> str:
    lines: list[str] = []
    b = info.basic
    lines.append("=" * 60)
    lines.append("【画像情報】")
    lines.append(f"  ファイル名: {b.filename}")
    lines.append(f"  解像度: {b.width} x {b.height} px")
    lines.append(f"  フォーマット: {b.format}")
    lines.append(f"  カラーモード: {b.mode}")
    if b.dpi:
        lines.append(f"  DPI: {b.dpi[0]} x {b.dpi[1]}")
    size_kb = b.file_size_bytes / 1024
    if size_kb > 1024:
        lines.append(f"  ファイルサイズ: {size_kb / 1024:.1f} MB")
    else:
        lines.append(f"  ファイルサイズ: {size_kb:.1f} KB")
    if b.has_alpha:
        lines.append("  アルファチャンネル: あり")
    if b.frames > 1:
        lines.append(f"  フレーム数: {b.frames}")
    lines.append("=" * 60)

    if info.exif:
        e = info.exif
        lines.append("\n【EXIF情報】")
        for label, val in [
            ("カメラ", e.camera), ("メーカー", e.maker), ("レンズ", e.lens),
            ("撮影日時", e.datetime), ("F値", e.f_number),
            ("シャッター速度", e.exposure_time), ("ISO", e.iso),
            ("焦点距離", e.focal_length), ("35mm換算", e.focal_length_35mm),
            ("露出補正", e.exposure_bias), ("測光モード", e.metering_mode),
            ("フラッシュ", e.flash), ("ホワイトバランス", e.white_balance),
            ("ソフトウェア", e.software),
        ]:
            if val:
                lines.append(f"  {label}: {val}")

        if e.gps_latitude and e.gps_longitude:
            lines.append(f"\n【GPS情報】")
            lines.append(f"  緯度: {e.gps_latitude}")
            lines.append(f"  経度: {e.gps_longitude}")

        if show_exif_detail and e.raw_tags:
            lines.append(f"\n【EXIF全タグ】")
            for tag_name, tag_value in sorted(e.raw_tags.items()):
                label = EXIF_TAG_LABELS.get(tag_name, tag_name)
                lines.append(f"  {label}: {tag_value}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON出力 (空値省略)
# ---------------------------------------------------------------------------

def _compact_dict(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None or v == "" or v == [] or v is False or v == 0:
            continue
        if isinstance(v, dict):
            v = _compact_dict(v)
            if not v:
                continue
        elif isinstance(v, list):
            v = [_compact_dict(i) if isinstance(i, dict) else i for i in v]
            v = [i for i in v if i]
            if not v:
                continue
        out[k] = v
    return out


def format_json(info: ImageInfo, show_exif_detail: bool = False) -> str:
    d = asdict(info)
    if not show_exif_detail and "exif" in d and d["exif"]:
        d["exif"].pop("raw_tags", None)
    return json.dumps(_compact_dict(d), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".bmp", ".tiff", ".tif", ".svg",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Image Reader - 画像メタデータ解析ツール")
    parser.add_argument("file", help="画像ファイルのパス")
    parser.add_argument("--exif", action="store_true", help="詳細EXIF情報を表示")
    parser.add_argument("--format", choices=["ai", "human", "json"], default="ai",
                        help="出力形式 (デフォルト: ai)")

    args = parser.parse_args()
    filepath = Path(args.file)

    if not filepath.exists():
        print(f"エラー: ファイルが見つかりません: {filepath}", file=sys.stderr)
        sys.exit(1)

    ext = filepath.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        print(f"エラー: サポートされていない画像形式です: {ext}\n"
              f"対応形式: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
              file=sys.stderr)
        sys.exit(1)

    info = parse_image(str(filepath))

    if args.format == "json":
        print(format_json(info, show_exif_detail=args.exif))
    elif args.format == "human":
        print(format_human(info, show_exif_detail=args.exif))
    else:
        print(format_ai(info, show_exif_detail=args.exif))


if __name__ == "__main__":
    main()
