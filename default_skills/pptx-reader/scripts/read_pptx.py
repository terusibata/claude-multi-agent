#!/usr/bin/env python3
"""
PPTX Reader - PowerPointファイル解析スクリプト (AI最適化版)

機能:
  - メタデータ抽出（作成者、日付等）
  - 全スライド / 範囲指定でのテキスト・ノート・テーブル・画像情報の抽出
  - キーワード検索（ヒットしたスライド番号とコンテキストを返す）
  - AI向けコンパクト出力 / JSON出力 / 人間向けテキスト出力

使用例:
  python3 read_pptx.py presentation.pptx                    # AI向け出力(デフォルト)
  python3 read_pptx.py presentation.pptx --format human      # 人間向け出力
  python3 read_pptx.py presentation.pptx --format json        # JSON出力(空値省略)
  python3 read_pptx.py presentation.pptx --slides 1-3
  python3 read_pptx.py presentation.pptx --slides 2,5,8
  python3 read_pptx.py presentation.pptx --search "キーワード"
  python3 read_pptx.py presentation.pptx --summary            # 目次のみ出力
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def ensure_python_pptx() -> None:
    """python-pptx がなければインストールする。"""
    try:
        import pptx  # noqa: F401
    except ImportError:
        print("python-pptx をインストール中...", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "python-pptx",
             "--break-system-packages", "-q"],
        )


ensure_python_pptx()

from pptx import Presentation  # noqa: E402
from pptx.enum.shapes import PP_PLACEHOLDER  # noqa: E402


# ---------------------------------------------------------------------------
# プレースホルダー種別マッピング (日本語)
# ---------------------------------------------------------------------------

PLACEHOLDER_LABELS: dict[int, str] = {
    0: "タイトル",
    1: "本文",
    2: "中央タイトル",
    3: "サブタイトル",
    4: "日付",
    5: "フッター",
    6: "スライド番号",
    10: "本文",
    11: "タイトル",
    12: "中央タイトル",
    13: "サブタイトル",
    14: "タイトル",
    15: "本文",
    16: "図",
}

LAYOUT_LABELS: dict[str, str] = {
    "Title Slide": "表紙",
    "タイトル スライド": "表紙",
    "Title and Content": "タイトル+本文",
    "タイトルとコンテンツ": "タイトル+本文",
    "Section Header": "セクション見出し",
    "セクション見出し": "セクション見出し",
    "Two Content": "2段組",
    "2 つのコンテンツ": "2段組",
    "Comparison": "比較",
    "比較": "比較",
    "Title Only": "タイトルのみ",
    "タイトルのみ": "タイトルのみ",
    "Blank": "白紙",
    "白紙": "白紙",
    "Content with Caption": "キャプション付き",
    "Picture with Caption": "図+キャプション",
}


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class RunInfo:
    text: str
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    size_pt: Optional[float] = None
    color_rgb: Optional[str] = None


@dataclass
class ParagraphInfo:
    text: str
    level: int = 0
    runs: list[RunInfo] = field(default_factory=list)


@dataclass
class TableInfo:
    rows: int = 0
    cols: int = 0
    cells: list[list[str]] = field(default_factory=list)
    has_header: bool = False


@dataclass
class ImageInfo:
    name: str = ""
    content_type: str = ""
    size_bytes: int = 0


@dataclass
class ShapeInfo:
    name: str = ""
    role: str = ""
    paragraphs: list[ParagraphInfo] = field(default_factory=list)
    table: Optional[TableInfo] = None
    image: Optional[ImageInfo] = None


@dataclass
class SlideInfo:
    number: int = 0
    layout: str = ""
    layout_label: str = ""
    title: str = ""
    notes: str = ""
    shapes: list[ShapeInfo] = field(default_factory=list)


@dataclass
class MetadataInfo:
    author: str = ""
    last_modified_by: str = ""
    created: str = ""
    modified: str = ""
    revision: Optional[int] = None
    title: str = ""
    subject: str = ""
    category: str = ""


@dataclass
class PresentationInfo:
    slide_count: int = 0
    width_inch: float = 0.0
    height_inch: float = 0.0
    metadata: MetadataInfo = field(default_factory=MetadataInfo)
    slides: list[SlideInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パーサー
# ---------------------------------------------------------------------------

def _safe_str(value: object) -> str:
    return str(value) if value else ""


def _get_placeholder_role(shape) -> str:
    if not shape.is_placeholder:
        return ""
    try:
        ph_idx = shape.placeholder_format.idx
        return PLACEHOLDER_LABELS.get(ph_idx, f"要素{ph_idx}")
    except Exception:
        return ""


def _parse_run(run) -> RunInfo:
    info = RunInfo(text=run.text)
    info.bold = run.font.bold if run.font.bold else None
    info.italic = run.font.italic if run.font.italic else None
    if run.font.size:
        info.size_pt = run.font.size.pt
    try:
        if run.font.color and run.font.color.type is not None and run.font.color.rgb:
            info.color_rgb = str(run.font.color.rgb)
    except (AttributeError, TypeError):
        pass
    return info


def _detect_table_header(table) -> bool:
    rows = list(table.rows)
    if len(rows) < 2:
        return False
    first_texts = [cell.text.strip() for cell in rows[0].cells]
    if all(0 < len(t) < 30 for t in first_texts):
        return True
    return False


def _parse_shape(shape) -> ShapeInfo:
    role = _get_placeholder_role(shape)
    si = ShapeInfo(name=shape.name, role=role)

    if shape.has_text_frame:
        for para in shape.text_frame.paragraphs:
            if para.text.strip():
                pi = ParagraphInfo(
                    text=para.text,
                    level=para.level if para.level else 0,
                    runs=[_parse_run(r) for r in para.runs],
                )
                si.paragraphs.append(pi)

    if shape.has_table:
        tbl = shape.table
        rows = list(tbl.rows)
        ti = TableInfo(
            rows=len(rows),
            cols=len(tbl.columns),
            cells=[[cell.text for cell in row.cells] for row in rows],
            has_header=_detect_table_header(tbl),
        )
        si.table = ti

    if shape.shape_type == 13:
        si.image = ImageInfo(
            name=shape.image.filename if hasattr(shape.image, "filename") else "",
            content_type=shape.image.content_type,
            size_bytes=len(shape.image.blob),
        )

    return si


def _extract_slide_title(slide) -> str:
    if slide.shapes.title and slide.shapes.title.has_text_frame:
        return slide.shapes.title.text_frame.text.strip()
    for shape in slide.shapes:
        if shape.has_text_frame and shape.text_frame.text.strip():
            return shape.text_frame.text.strip()[:50]
    return ""


def _parse_slide(slide, number: int) -> SlideInfo:
    layout_name = slide.slide_layout.name
    si = SlideInfo(
        number=number,
        layout=layout_name,
        layout_label=LAYOUT_LABELS.get(layout_name, layout_name),
        title=_extract_slide_title(slide),
    )

    if slide.has_notes_slide:
        notes_text = slide.notes_slide.notes_text_frame.text
        if notes_text.strip():
            si.notes = notes_text.strip()

    for shape in slide.shapes:
        shape_info = _parse_shape(shape)
        if shape_info.paragraphs or shape_info.table or shape_info.image:
            si.shapes.append(shape_info)

    return si


def parse_pptx(filepath: str) -> PresentationInfo:
    prs = Presentation(filepath)
    props = prs.core_properties

    info = PresentationInfo(
        slide_count=len(prs.slides),
        width_inch=round(prs.slide_width / 914400, 2),
        height_inch=round(prs.slide_height / 914400, 2),
        metadata=MetadataInfo(
            author=_safe_str(props.author),
            last_modified_by=_safe_str(props.last_modified_by),
            created=_safe_str(props.created),
            modified=_safe_str(props.modified),
            revision=props.revision,
            title=_safe_str(props.title),
            subject=_safe_str(props.subject),
            category=_safe_str(props.category),
        ),
    )

    for i, slide in enumerate(prs.slides, 1):
        info.slides.append(_parse_slide(slide, i))

    return info


# ---------------------------------------------------------------------------
# スライド範囲パーサー
# ---------------------------------------------------------------------------

def parse_slide_range(spec: str, total: int) -> list[int]:
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            s, e = part.split("-", 1)
            result.update(range(max(1, int(s)), min(total, int(e)) + 1))
        else:
            n = int(part)
            if 1 <= n <= total:
                result.add(n)
    return sorted(result)


# ---------------------------------------------------------------------------
# キーワード検索
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    slide_number: int
    shape_name: str
    role: str
    text: str
    context: str


def search_presentation(info: PresentationInfo, keyword: str) -> list[SearchHit]:
    kw = keyword.lower()
    hits: list[SearchHit] = []

    for slide in info.slides:
        if kw in slide.notes.lower():
            hits.append(SearchHit(
                slide_number=slide.number, shape_name="ノート", role="ノート",
                text=slide.notes, context=_extract_context(slide.notes, kw),
            ))
        for shape in slide.shapes:
            for para in shape.paragraphs:
                if kw in para.text.lower():
                    hits.append(SearchHit(
                        slide_number=slide.number, shape_name=shape.name,
                        role=shape.role or "コンテンツ",
                        text=para.text, context=_extract_context(para.text, kw),
                    ))
            if shape.table:
                for ri, row in enumerate(shape.table.cells):
                    for ci, cell in enumerate(row):
                        if kw in cell.lower():
                            hits.append(SearchHit(
                                slide_number=slide.number,
                                shape_name=f"{shape.name}[行{ri},列{ci}]",
                                role="表", text=cell, context=cell,
                            ))
    return hits


def _extract_context(text: str, kw: str, window: int = 30) -> str:
    idx = text.lower().find(kw)
    if idx == -1:
        return text[:60]
    s = max(0, idx - window)
    e = min(len(text), idx + len(kw) + window)
    pre = "…" if s > 0 else ""
    suf = "…" if e < len(text) else ""
    return f"{pre}{text[s:e]}{suf}"


# ---------------------------------------------------------------------------
# AI向けコンパクト出力
# ---------------------------------------------------------------------------

def format_ai(info: PresentationInfo, slides_filter: Optional[list[int]] = None) -> str:
    lines: list[str] = []
    m = info.metadata

    # ヘッダー (1行に凝縮)
    meta_parts: list[str] = [f"{info.slide_count}枚"]
    if m.title:
        meta_parts.insert(0, m.title)
    if m.author:
        meta_parts.append(f"作成:{m.author}")
    if m.modified:
        meta_parts.append(f"更新:{m.modified}")
    lines.append(f"[PPTX] {' | '.join(meta_parts)}")

    # 目次
    toc_parts: list[str] = []
    for s in info.slides:
        title = s.title or s.layout_label
        toc_parts.append(f"S{s.number}:{title}")
    lines.append(f"[目次] {' / '.join(toc_parts)}")
    lines.append("")

    # 各スライド詳細
    for slide in info.slides:
        if slides_filter and slide.number not in slides_filter:
            continue

        header = f"== S{slide.number} ({slide.layout_label})"
        if slide.title:
            header += f" 「{slide.title}」"
        lines.append(header)

        if slide.notes:
            lines.append(f"  [ノート] {slide.notes}")

        for shape in slide.shapes:
            role_tag = f"[{shape.role}]" if shape.role else f"[{shape.name}]"

            if shape.paragraphs:
                if len(shape.paragraphs) == 1:
                    p = shape.paragraphs[0]
                    bold_runs = [r.text for r in p.runs if r.bold]
                    fmt = f" (強調: {'、'.join(bold_runs)})" if bold_runs else ""
                    lines.append(f"  {role_tag} {p.text}{fmt}")
                else:
                    lines.append(f"  {role_tag}")
                    for p in shape.paragraphs:
                        indent = "  " * p.level
                        bold_runs = [r.text for r in p.runs if r.bold]
                        fmt = f" (強調: {'、'.join(bold_runs)})" if bold_runs else ""
                        lines.append(f"    {indent}{p.text}{fmt}")

            if shape.table:
                t = shape.table
                lines.append(f"  [表] {t.rows}行x{t.cols}列")
                for ri, row in enumerate(t.cells):
                    prefix = "  H" if (ri == 0 and t.has_header) else f"  {ri}"
                    lines.append(f"    {prefix}| {' | '.join(row)}")

            if shape.image:
                img = shape.image
                lines.append(f"  [画像] {img.name} ({img.content_type}, {img.size_bytes // 1024}KB)")

        lines.append("")

    return "\n".join(lines).rstrip()


def format_search_ai(hits: list[SearchHit], keyword: str) -> str:
    if not hits:
        return f"検索: '{keyword}' → 該当なし"
    lines = [f"検索: '{keyword}' → {len(hits)}件"]
    for h in hits:
        lines.append(f"  S{h.slide_number} [{h.role}] {h.context}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 人間向け出力
# ---------------------------------------------------------------------------

def format_human(info: PresentationInfo, slides_filter: Optional[list[int]] = None) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("【プレゼンテーション情報】")
    lines.append(f"  スライド数: {info.slide_count}")
    lines.append(f"  サイズ: {info.width_inch} x {info.height_inch} inch")
    m = info.metadata
    for label, val in [("タイトル", m.title), ("作成者", m.author),
                       ("最終更新者", m.last_modified_by), ("作成日", m.created),
                       ("更新日", m.modified)]:
        if val:
            lines.append(f"  {label}: {val}")
    if m.revision:
        lines.append(f"  リビジョン: {m.revision}")
    lines.append("=" * 60)

    for slide in info.slides:
        if slides_filter and slide.number not in slides_filter:
            continue
        lines.append(f"\n--- スライド {slide.number} ({slide.layout_label}) ---")
        if slide.notes:
            lines.append(f"  ノート: {slide.notes}")
        for shape in slide.shapes:
            role = f" ({shape.role})" if shape.role else ""
            lines.append(f"\n  [{shape.name}]{role}")
            for para in shape.paragraphs:
                lines.append(f"    {'  ' * para.level}{para.text}")
            if shape.table:
                t = shape.table
                hdr = " (ヘッダー付き)" if t.has_header else ""
                lines.append(f"    表: {t.rows}行 x {t.cols}列{hdr}")
                for ri, row in enumerate(t.cells):
                    label = "H" if (ri == 0 and t.has_header) else str(ri)
                    lines.append(f"      {label}: {row}")
            if shape.image:
                img = shape.image
                lines.append(f"    画像: {img.name} ({img.content_type}, {img.size_bytes:,}B)")
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def format_search_human(hits: list[SearchHit], keyword: str) -> str:
    if not hits:
        return f"キーワード '{keyword}' は見つかりませんでした。"
    lines = [f"検索結果: '{keyword}' ({len(hits)}件)", "=" * 40]
    for i, h in enumerate(hits, 1):
        lines.append(f"  [{i}] スライド{h.slide_number} - {h.role}: {h.context}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON出力 (空値省略)
# ---------------------------------------------------------------------------

def _compact_dict(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None or v == "" or v == [] or v is False:
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


def format_json(info: PresentationInfo, slides_filter: Optional[list[int]] = None) -> str:
    d = asdict(info)
    if slides_filter:
        d["slides"] = [s for s in d["slides"] if s["number"] in slides_filter]
    return json.dumps(_compact_dict(d), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 目次のみ出力
# ---------------------------------------------------------------------------

def format_summary(info: PresentationInfo) -> str:
    lines: list[str] = []
    m = info.metadata
    meta_parts = [f"{info.slide_count}枚"]
    if m.title:
        meta_parts.insert(0, m.title)
    if m.author:
        meta_parts.append(f"作成:{m.author}")
    lines.append(f"[PPTX] {' | '.join(meta_parts)}")
    lines.append("")
    for s in info.slides:
        notes_flag = " *ノート有" if s.notes else ""
        table_count = sum(1 for sh in s.shapes if sh.table)
        image_count = sum(1 for sh in s.shapes if sh.image)
        extras = []
        if table_count:
            extras.append(f"表{table_count}")
        if image_count:
            extras.append(f"画像{image_count}")
        extra_str = f" [{','.join(extras)}]" if extras else ""
        title = s.title or "(テキストなし)"
        lines.append(f"  S{s.number} ({s.layout_label}) {title}{extra_str}{notes_flag}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="PPTX Reader - PowerPointファイル解析ツール")
    parser.add_argument("file", help="PPTXファイルのパス")
    parser.add_argument("--slides", help="スライド範囲 (例: 1-3, 2,5,8)")
    parser.add_argument("--search", help="キーワード検索")
    parser.add_argument("--format", choices=["ai", "human", "json"], default="ai",
                        help="出力形式 (デフォルト: ai)")
    parser.add_argument("--summary", action="store_true", help="目次のみ出力")

    args = parser.parse_args()
    filepath = Path(args.file)

    if not filepath.exists():
        print(f"エラー: ファイルが見つかりません: {filepath}", file=sys.stderr)
        sys.exit(1)

    info = parse_pptx(str(filepath))

    slides_filter: Optional[list[int]] = None
    if args.slides:
        slides_filter = parse_slide_range(args.slides, info.slide_count)

    if args.summary:
        print(format_summary(info))
        return

    if args.search:
        hits = search_presentation(info, args.search)
        if args.format == "json":
            print(json.dumps([_compact_dict(asdict(h)) for h in hits], ensure_ascii=False, indent=2))
        elif args.format == "human":
            print(format_search_human(hits, args.search))
        else:
            print(format_search_ai(hits, args.search))
        return

    if args.format == "json":
        print(format_json(info, slides_filter))
    elif args.format == "human":
        print(format_human(info, slides_filter))
    else:
        print(format_ai(info, slides_filter))


if __name__ == "__main__":
    main()