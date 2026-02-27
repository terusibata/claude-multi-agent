#!/usr/bin/env python3
"""
PDF Reader - PDFファイル解析スクリプト (AI最適化版)

機能:
  - メタデータ抽出（作成者、日付、ページ数等）
  - 目次（アウトライン）抽出
  - 全ページ / 範囲指定でのテキスト抽出
  - キーワード検索（ヒットしたページ番号とコンテキストを返す）
  - ページを画像として保存（図表確認用）
  - AI向けコンパクト出力 / JSON出力 / 人間向けテキスト出力

使用例:
  python3 read_pdf.py document.pdf                       # AI向け出力(デフォルト)
  python3 read_pdf.py document.pdf --format human         # 人間向け出力
  python3 read_pdf.py document.pdf --format json           # JSON出力(空値省略)
  python3 read_pdf.py document.pdf --pages 1-5
  python3 read_pdf.py document.pdf --pages 2,5,8
  python3 read_pdf.py document.pdf --search "キーワード"
  python3 read_pdf.py document.pdf --summary               # 概要のみ出力
  python3 read_pdf.py document.pdf --pages 1-3 --images    # ページ画像保存
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def ensure_pymupdf() -> None:
    """PyMuPDF がなければインストールする。"""
    try:
        import fitz  # noqa: F401
    except ImportError:
        print("PyMuPDF をインストール中...", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "PyMuPDF",
             "--break-system-packages", "-q"],
        )


ensure_pymupdf()

import fitz  # noqa: E402


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class TocEntry:
    level: int
    title: str
    page: int


@dataclass
class PageInfo:
    number: int
    char_count: int = 0
    text: str = ""


@dataclass
class MetadataInfo:
    title: str = ""
    author: str = ""
    subject: str = ""
    creator: str = ""
    producer: str = ""
    created: str = ""
    modified: str = ""
    encrypted: bool = False


@dataclass
class PdfInfo:
    page_count: int = 0
    metadata: MetadataInfo = field(default_factory=MetadataInfo)
    toc: list[TocEntry] = field(default_factory=list)
    pages: list[PageInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パーサー
# ---------------------------------------------------------------------------

def _safe_str(value: object) -> str:
    return str(value) if value else ""


def _format_date(date_str: str) -> str:
    """PDF日付形式 (D:YYYYMMDDHHmmSS) を読みやすい形式に変換する。"""
    if not date_str:
        return ""
    s = date_str
    if s.startswith("D:"):
        s = s[2:]
    # Remove timezone info
    for sep in ("+", "-", "Z"):
        if sep in s[8:]:
            s = s[:s.index(sep, 8)]
    s = s.replace("'", "")
    if len(s) >= 14:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"
    elif len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return date_str


def parse_pdf(filepath: str) -> PdfInfo:
    doc = fitz.open(filepath)

    meta = doc.metadata or {}
    info = PdfInfo(
        page_count=len(doc),
        metadata=MetadataInfo(
            title=_safe_str(meta.get("title")),
            author=_safe_str(meta.get("author")),
            subject=_safe_str(meta.get("subject")),
            creator=_safe_str(meta.get("creator")),
            producer=_safe_str(meta.get("producer")),
            created=_format_date(_safe_str(meta.get("creationDate"))),
            modified=_format_date(_safe_str(meta.get("modDate"))),
            encrypted=doc.is_encrypted,
        ),
    )

    # TOC (outline)
    toc = doc.get_toc(simple=True)
    for level, title, page_num in toc:
        info.toc.append(TocEntry(level=level, title=title, page=page_num))

    # Pages
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text("text").strip()
        info.pages.append(PageInfo(
            number=i + 1,
            char_count=len(text),
            text=text,
        ))

    doc.close()
    return info


# ---------------------------------------------------------------------------
# ページ範囲パーサー
# ---------------------------------------------------------------------------

def parse_page_range(spec: str, total: int) -> list[int]:
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
    page_number: int
    text: str
    context: str


def search_pdf(info: PdfInfo, keyword: str) -> list[SearchHit]:
    kw = keyword.lower()
    hits: list[SearchHit] = []

    for page in info.pages:
        if kw in page.text.lower():
            hits.append(SearchHit(
                page_number=page.number,
                text=page.text[:100],
                context=_extract_context(page.text, kw),
            ))

    return hits


def _extract_context(text: str, kw: str, window: int = 40) -> str:
    idx = text.lower().find(kw)
    if idx == -1:
        return text[:80]
    s = max(0, idx - window)
    e = min(len(text), idx + len(kw) + window)
    pre = "…" if s > 0 else ""
    suf = "…" if e < len(text) else ""
    return f"{pre}{text[s:e]}{suf}"


# ---------------------------------------------------------------------------
# 画像保存
# ---------------------------------------------------------------------------

def save_pages_as_images(filepath: str, pages: list[int], dpi: int = 150) -> list[str]:
    """指定ページをPNG画像として保存し、パスのリストを返す。"""
    doc = fitz.open(filepath)
    saved: list[str] = []
    base = Path(filepath).stem

    for page_num in pages:
        if page_num < 1 or page_num > len(doc):
            continue
        page = doc[page_num - 1]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        out_path = Path(filepath).parent / f"{base}_p{page_num}.png"
        pix.save(str(out_path))
        saved.append(str(out_path))

    doc.close()
    return saved


# ---------------------------------------------------------------------------
# AI向けコンパクト出力
# ---------------------------------------------------------------------------

def format_ai(info: PdfInfo, pages_filter: Optional[list[int]] = None) -> str:
    lines: list[str] = []
    m = info.metadata

    # ヘッダー
    meta_parts: list[str] = [f"{info.page_count}p"]
    if m.title:
        meta_parts.insert(0, m.title)
    if m.author:
        meta_parts.append(f"作成:{m.author}")
    if m.modified:
        meta_parts.append(f"更新:{m.modified}")
    elif m.created:
        meta_parts.append(f"作成日:{m.created}")
    if m.encrypted:
        meta_parts.append("暗号化")
    lines.append(f"[PDF] {' | '.join(meta_parts)}")

    # 目次
    if info.toc:
        toc_parts: list[str] = []
        for entry in info.toc:
            if entry.level <= 2:
                toc_parts.append(f"{entry.title}(P{entry.page})")
        if toc_parts:
            lines.append(f"[目次] {' / '.join(toc_parts)}")

    # ページ概要
    summary_parts: list[str] = []
    for page in info.pages:
        label = f"P{page.number}:{page.char_count}字"
        if page.char_count < 200:
            label += "(図表主体)"
        summary_parts.append(label)
    # Show first 20 pages in summary
    if len(summary_parts) > 20:
        shown = summary_parts[:20]
        shown.append(f"...他{len(summary_parts) - 20}p")
        lines.append(f"[概要] {' / '.join(shown)}")
    else:
        lines.append(f"[概要] {' / '.join(summary_parts)}")
    lines.append("")

    # 各ページ詳細
    target_pages = pages_filter or [p.number for p in info.pages[:10]]
    for page in info.pages:
        if page.number not in target_pages:
            continue
        lines.append(f"== P{page.number}")
        if page.text:
            for line in page.text.split("\n"):
                stripped = line.strip()
                if stripped:
                    lines.append(f"  {stripped}")
        else:
            lines.append("  (テキストなし)")
        lines.append("")

    if not pages_filter and info.page_count > 10:
        lines.append(f"--- 残り{info.page_count - 10}p。取得: --pages \"11-{info.page_count}\" ---")

    return "\n".join(lines).rstrip()


def format_search_ai(hits: list[SearchHit], keyword: str) -> str:
    if not hits:
        return f"検索: '{keyword}' → 該当なし"
    lines = [f"検索: '{keyword}' → {len(hits)}件"]
    for h in hits:
        lines.append(f"  P{h.page_number} {h.context}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 人間向け出力
# ---------------------------------------------------------------------------

def format_human(info: PdfInfo, pages_filter: Optional[list[int]] = None) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("【PDF情報】")
    lines.append(f"  ページ数: {info.page_count}")
    m = info.metadata
    for label, val in [("タイトル", m.title), ("作成者", m.author),
                       ("件名", m.subject), ("作成日", m.created),
                       ("更新日", m.modified), ("作成ツール", m.creator)]:
        if val:
            lines.append(f"  {label}: {val}")
    if m.encrypted:
        lines.append("  暗号化: あり")
    lines.append("=" * 60)

    if info.toc:
        lines.append("\n【目次】")
        for entry in info.toc:
            indent = "  " * entry.level
            lines.append(f"  {indent}{entry.title} (P{entry.page})")

    target_pages = pages_filter or [p.number for p in info.pages[:10]]
    for page in info.pages:
        if page.number not in target_pages:
            continue
        lines.append(f"\n--- ページ {page.number} ({page.char_count}字) ---")
        if page.text:
            lines.append(page.text)
        else:
            lines.append("  (テキストなし)")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def format_search_human(hits: list[SearchHit], keyword: str) -> str:
    if not hits:
        return f"キーワード '{keyword}' は見つかりませんでした。"
    lines = [f"検索結果: '{keyword}' ({len(hits)}件)", "=" * 40]
    for i, h in enumerate(hits, 1):
        lines.append(f"  [{i}] ページ{h.page_number}: {h.context}")
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


def format_json(info: PdfInfo, pages_filter: Optional[list[int]] = None) -> str:
    d = asdict(info)
    if pages_filter:
        d["pages"] = [p for p in d["pages"] if p["number"] in pages_filter]
    return json.dumps(_compact_dict(d), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 概要のみ出力
# ---------------------------------------------------------------------------

def format_summary(info: PdfInfo) -> str:
    lines: list[str] = []
    m = info.metadata
    meta_parts = [f"{info.page_count}p"]
    if m.title:
        meta_parts.insert(0, m.title)
    if m.author:
        meta_parts.append(f"作成:{m.author}")
    if m.modified:
        meta_parts.append(f"更新:{m.modified}")
    lines.append(f"[PDF] {' | '.join(meta_parts)}")

    if info.toc:
        lines.append("")
        lines.append("[目次]")
        for entry in info.toc:
            indent = "  " * (entry.level - 1)
            lines.append(f"  {indent}{entry.title} (P{entry.page})")

    lines.append("")
    for page in info.pages:
        label = f"  P{page.number}: {page.char_count}字"
        if page.char_count < 200:
            label += " (図表主体)"
        elif page.char_count > 3000:
            label += " (テキスト大量)"
        # Show first line as preview
        first_line = ""
        if page.text:
            for line in page.text.split("\n"):
                stripped = line.strip()
                if stripped:
                    first_line = stripped[:50]
                    break
        if first_line:
            label += f" {first_line}"
        lines.append(label)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="PDF Reader - PDFファイル解析ツール")
    parser.add_argument("file", help="PDFファイルのパス")
    parser.add_argument("--pages", help="ページ範囲 (例: 1-5, 2,5,8)")
    parser.add_argument("--search", help="キーワード検索")
    parser.add_argument("--format", choices=["ai", "human", "json"], default="ai",
                        help="出力形式 (デフォルト: ai)")
    parser.add_argument("--summary", action="store_true", help="概要のみ出力")
    parser.add_argument("--images", action="store_true", help="ページを画像として保存")
    parser.add_argument("--dpi", type=int, default=150, help="画像出力時のDPI (デフォルト: 150)")

    args = parser.parse_args()
    filepath = Path(args.file)

    if not filepath.exists():
        print(f"エラー: ファイルが見つかりません: {filepath}", file=sys.stderr)
        sys.exit(1)

    if not filepath.suffix.lower() == ".pdf":
        print(f"エラー: PDFファイルではありません: {filepath}", file=sys.stderr)
        sys.exit(1)

    info = parse_pdf(str(filepath))

    pages_filter: Optional[list[int]] = None
    if args.pages:
        pages_filter = parse_page_range(args.pages, info.page_count)

    # 画像保存モード
    if args.images:
        target = pages_filter or list(range(1, min(info.page_count + 1, 4)))
        saved = save_pages_as_images(str(filepath), target, args.dpi)
        if saved:
            print(f"画像保存完了: {len(saved)}ページ")
            for p in saved:
                print(f"  {p}")
        else:
            print("画像保存: 対象ページがありません")
        return

    if args.summary:
        print(format_summary(info))
        return

    if args.search:
        hits = search_pdf(info, args.search)
        if args.format == "json":
            print(json.dumps([_compact_dict(asdict(h)) for h in hits], ensure_ascii=False, indent=2))
        elif args.format == "human":
            print(format_search_human(hits, args.search))
        else:
            print(format_search_ai(hits, args.search))
        return

    if args.format == "json":
        print(format_json(info, pages_filter))
    elif args.format == "human":
        print(format_human(info, pages_filter))
    else:
        print(format_ai(info, pages_filter))


if __name__ == "__main__":
    main()
