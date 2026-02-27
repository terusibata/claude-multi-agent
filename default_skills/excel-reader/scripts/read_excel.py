#!/usr/bin/env python3
"""
Excel Reader - Excelファイル解析スクリプト (AI最適化版)

機能:
  - シート一覧と基本情報の取得
  - セルデータのCSV出力（行範囲指定可能）
  - 全シート横断キーワード検索
  - マージセル対応
  - AI向けコンパクト出力 / JSON出力 / 人間向けテキスト出力

使用例:
  python3 read_excel.py workbook.xlsx                        # AI向け出力(デフォルト)
  python3 read_excel.py workbook.xlsx --format human          # 人間向け出力
  python3 read_excel.py workbook.xlsx --format json            # JSON出力(空値省略)
  python3 read_excel.py workbook.xlsx --sheet "Sheet1"
  python3 read_excel.py workbook.xlsx --rows 1-50
  python3 read_excel.py workbook.xlsx --search "キーワード"
  python3 read_excel.py workbook.xlsx --summary                # シート一覧のみ
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, time
from pathlib import Path
from typing import Optional


def ensure_openpyxl() -> None:
    """openpyxl がなければインストールする。"""
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl をインストール中...", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "openpyxl",
             "--break-system-packages", "-q"],
        )


ensure_openpyxl()

import openpyxl  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class SheetInfo:
    name: str
    rows: int = 0
    cols: int = 0
    has_merged_cells: bool = False
    merged_count: int = 0


@dataclass
class WorkbookInfo:
    filename: str = ""
    sheet_count: int = 0
    sheets: list[SheetInfo] = field(default_factory=list)


@dataclass
class SheetData:
    name: str
    total_rows: int = 0
    total_cols: int = 0
    start_row: int = 1
    end_row: int = 0
    has_more: bool = False
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class SearchHit:
    sheet_name: str
    row: int
    col: int
    col_letter: str
    value: str
    context: list[str]


# ---------------------------------------------------------------------------
# セル値のフォーマット
# ---------------------------------------------------------------------------

def _cell_to_str(cell) -> str:
    """セル値を文字列に変換する。"""
    if cell.value is None:
        return ""
    v = cell.value
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return str(v)
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


# ---------------------------------------------------------------------------
# パーサー
# ---------------------------------------------------------------------------

def parse_workbook_info(filepath: str) -> WorkbookInfo:
    """ワークブックのシート一覧と基本情報を取得する。"""
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    info = WorkbookInfo(
        filename=Path(filepath).name,
        sheet_count=len(wb.sheetnames),
    )

    for name in wb.sheetnames:
        ws = wb[name]
        si = SheetInfo(name=name)
        si.rows = ws.max_row or 0
        si.cols = ws.max_column or 0
        info.sheets.append(si)

    wb.close()

    # Re-open in normal mode to check merged cells
    wb2 = openpyxl.load_workbook(filepath, data_only=True)
    for si in info.sheets:
        ws2 = wb2[si.name]
        if ws2.merged_cells.ranges:
            si.has_merged_cells = True
            si.merged_count = len(ws2.merged_cells.ranges)
    wb2.close()

    return info


def read_sheet_data(
    filepath: str,
    sheet_name: Optional[str] = None,
    start_row: int = 1,
    end_row: Optional[int] = None,
    max_rows: int = 100,
) -> SheetData:
    """シートデータを読み取る。マージセル対応。"""
    wb = openpyxl.load_workbook(filepath, data_only=True)

    if sheet_name:
        if sheet_name not in wb.sheetnames:
            wb.close()
            raise ValueError(f"シート '{sheet_name}' が見つかりません。"
                             f"利用可能: {', '.join(wb.sheetnames)}")
        ws = wb[sheet_name]
    else:
        ws = wb.active or wb[wb.sheetnames[0]]

    # Build merged cell value map
    merged_values: dict[tuple[int, int], str] = {}
    for merge_range in ws.merged_cells.ranges:
        top_left = ws.cell(merge_range.min_row, merge_range.min_col)
        val = _cell_to_str(top_left)
        for row in range(merge_range.min_row, merge_range.max_row + 1):
            for col in range(merge_range.min_col, merge_range.max_col + 1):
                merged_values[(row, col)] = val

    total_rows = ws.max_row or 0
    total_cols = ws.max_column or 0

    # Calculate row range
    actual_start = max(1, start_row)
    if end_row:
        actual_end = min(end_row, total_rows)
    else:
        actual_end = min(actual_start + max_rows - 1, total_rows)

    has_more = actual_end < total_rows

    data = SheetData(
        name=ws.title,
        total_rows=total_rows,
        total_cols=total_cols,
        start_row=actual_start,
        end_row=actual_end,
        has_more=has_more,
    )

    for row_idx in range(actual_start, actual_end + 1):
        row_data: list[str] = []
        for col_idx in range(1, total_cols + 1):
            if (row_idx, col_idx) in merged_values:
                row_data.append(merged_values[(row_idx, col_idx)])
            else:
                cell = ws.cell(row_idx, col_idx)
                row_data.append(_cell_to_str(cell))
        data.rows.append(row_data)

    wb.close()
    return data


# ---------------------------------------------------------------------------
# 行範囲パーサー
# ---------------------------------------------------------------------------

def parse_row_range(spec: str, total: int) -> tuple[int, Optional[int]]:
    """行範囲を解析し、(start, end) のタプルを返す。"""
    parts = spec.split(",")
    if len(parts) == 1 and "-" in parts[0]:
        s, e = parts[0].strip().split("-", 1)
        return max(1, int(s)), min(total, int(e))
    else:
        # Comma-separated: return range covering all specified rows
        rows = sorted(int(p.strip()) for p in parts)
        return rows[0], rows[-1]


# ---------------------------------------------------------------------------
# キーワード検索
# ---------------------------------------------------------------------------

def search_workbook(filepath: str, keyword: str) -> list[SearchHit]:
    """全シートを横断してキーワード検索する。"""
    wb = openpyxl.load_workbook(filepath, data_only=True)
    kw = keyword.lower()
    hits: list[SearchHit] = []

    for name in wb.sheetnames:
        ws = wb[name]
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                                max_col=ws.max_column):
            for cell in row:
                val = _cell_to_str(cell)
                if val and kw in val.lower():
                    # Build context: surrounding cells in the same row
                    context = []
                    for c in ws[cell.row]:
                        cv = _cell_to_str(c)
                        if cv:
                            context.append(cv)

                    hits.append(SearchHit(
                        sheet_name=name,
                        row=cell.row,
                        col=cell.column,
                        col_letter=get_column_letter(cell.column),
                        value=val,
                        context=context,
                    ))

    wb.close()
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
# CSV出力ヘルパー
# ---------------------------------------------------------------------------

def rows_to_csv(rows: list[list[str]]) -> str:
    """行データをRFC 4180準拠のCSV文字列に変換する。"""
    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow(row)
    return output.getvalue().rstrip()


# ---------------------------------------------------------------------------
# AI向けコンパクト出力
# ---------------------------------------------------------------------------

def format_ai_summary(info: WorkbookInfo) -> str:
    lines: list[str] = []
    lines.append(f"[XLSX] {info.filename} | {info.sheet_count}シート")
    sheet_parts: list[str] = []
    for i, s in enumerate(info.sheets, 1):
        merged = f",マージ{s.merged_count}" if s.has_merged_cells else ""
        sheet_parts.append(f"S{i}:{s.name}({s.rows}行x{s.cols}列{merged})")
    lines.append(f"[シート] {' / '.join(sheet_parts)}")
    return "\n".join(lines)


def format_ai_data(info: WorkbookInfo, data: SheetData) -> str:
    lines: list[str] = []
    lines.append(f"[XLSX] {info.filename} | {info.sheet_count}シート")
    sheet_parts: list[str] = []
    for i, s in enumerate(info.sheets, 1):
        sheet_parts.append(f"S{i}:{s.name}({s.rows}行x{s.cols}列)")
    lines.append(f"[シート] {' / '.join(sheet_parts)}")
    lines.append("")

    range_str = f"{data.start_row}-{data.end_row}" if data.start_row != 1 or data.has_more else ""
    range_label = f", 表示:{range_str}行" if range_str else ""
    lines.append(f"== {data.name} ({data.total_rows}行x{data.total_cols}列{range_label})")

    # CSV data
    lines.append(rows_to_csv(data.rows))

    if data.has_more:
        remaining = data.total_rows - data.end_row
        lines.append(f"--- 残り{remaining}行。取得: --rows \"{data.end_row + 1}-{data.total_rows}\" ---")

    return "\n".join(lines)


def format_search_ai(hits: list[SearchHit], keyword: str) -> str:
    if not hits:
        return f"検索: '{keyword}' → 該当なし"
    lines = [f"検索: '{keyword}' → {len(hits)}件"]
    for h in hits:
        ctx = " | ".join(h.context[:5])
        lines.append(f"  {h.sheet_name}!{h.col_letter}{h.row} [{h.value}] 行: {ctx}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 人間向け出力
# ---------------------------------------------------------------------------

def format_human_summary(info: WorkbookInfo) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"【ワークブック情報】 {info.filename}")
    lines.append(f"  シート数: {info.sheet_count}")
    for i, s in enumerate(info.sheets, 1):
        merged = f" (マージ{s.merged_count}個)" if s.has_merged_cells else ""
        lines.append(f"  {i}. {s.name}: {s.rows}行 x {s.cols}列{merged}")
    lines.append("=" * 60)
    return "\n".join(lines)


def format_human_data(info: WorkbookInfo, data: SheetData) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"【ワークブック】 {info.filename}")
    lines.append(f"【シート】 {data.name} ({data.total_rows}行 x {data.total_cols}列)")
    lines.append(f"【表示範囲】 行 {data.start_row} ～ {data.end_row}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(rows_to_csv(data.rows))
    if data.has_more:
        remaining = data.total_rows - data.end_row
        lines.append(f"\n--- 残り{remaining}行 ---")
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def format_search_human(hits: list[SearchHit], keyword: str) -> str:
    if not hits:
        return f"キーワード '{keyword}' は見つかりませんでした。"
    lines = [f"検索結果: '{keyword}' ({len(hits)}件)", "=" * 40]
    for i, h in enumerate(hits, 1):
        ctx = " | ".join(h.context[:5])
        lines.append(f"  [{i}] {h.sheet_name}!{h.col_letter}{h.row}: {h.value}")
        lines.append(f"       行データ: {ctx}")
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


def format_json_summary(info: WorkbookInfo) -> str:
    return json.dumps(_compact_dict(asdict(info)), ensure_ascii=False, indent=2)


def format_json_data(data: SheetData) -> str:
    return json.dumps(_compact_dict(asdict(data)), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Excel Reader - Excelファイル解析ツール")
    parser.add_argument("file", help="Excelファイルのパス")
    parser.add_argument("--sheet", help="シート名を指定")
    parser.add_argument("--rows", help="行範囲 (例: 1-100, 50,60,70)")
    parser.add_argument("--search", help="キーワード検索")
    parser.add_argument("--format", choices=["ai", "human", "json"], default="ai",
                        help="出力形式 (デフォルト: ai)")
    parser.add_argument("--summary", action="store_true", help="シート一覧のみ出力")
    parser.add_argument("--max-rows", type=int, default=100,
                        help="最大行数 (デフォルト: 100)")

    args = parser.parse_args()
    filepath = Path(args.file)

    if not filepath.exists():
        print(f"エラー: ファイルが見つかりません: {filepath}", file=sys.stderr)
        sys.exit(1)

    ext = filepath.suffix.lower()
    if ext == ".xls":
        print("エラー: .xls（旧形式）は非対応です。\n"
              ".xlsx（Office Open XML）形式に変換してください。\n\n"
              "対処方法:\n"
              "1. Excelで .xlsx 形式に変換して再アップロード\n"
              "2. LibreOffice で .xlsx 形式に変換", file=sys.stderr)
        sys.exit(1)

    if ext not in (".xlsx",):
        print(f"エラー: Excelファイルではありません: {filepath}", file=sys.stderr)
        sys.exit(1)

    # シート一覧取得
    info = parse_workbook_info(str(filepath))

    if args.summary:
        if args.format == "json":
            print(format_json_summary(info))
        elif args.format == "human":
            print(format_human_summary(info))
        else:
            print(format_ai_summary(info))
        return

    if args.search:
        hits = search_workbook(str(filepath), args.search)
        if args.format == "json":
            print(json.dumps([_compact_dict(asdict(h)) for h in hits],
                             ensure_ascii=False, indent=2))
        elif args.format == "human":
            print(format_search_human(hits, args.search))
        else:
            print(format_search_ai(hits, args.search))
        return

    # データ取得
    start_row = 1
    end_row: Optional[int] = None

    if args.rows:
        # Peek at total rows for range parsing
        sheet_name = args.sheet or info.sheets[0].name
        total = next((s.rows for s in info.sheets if s.name == sheet_name), 0)
        if total:
            start_row, end_row = parse_row_range(args.rows, total)

    data = read_sheet_data(
        str(filepath),
        sheet_name=args.sheet,
        start_row=start_row,
        end_row=end_row,
        max_rows=args.max_rows,
    )

    if args.format == "json":
        print(format_json_data(data))
    elif args.format == "human":
        print(format_human_data(info, data))
    else:
        print(format_ai_data(info, data))


if __name__ == "__main__":
    main()
