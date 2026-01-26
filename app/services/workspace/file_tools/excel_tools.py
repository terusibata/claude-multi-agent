"""
Excelファイル用ツール

inspect_excel_file: 構造確認（シート一覧、ヘッダー）
read_excel_sheet: データ取得（範囲指定可能）
"""

import io
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.services.workspace_service import WorkspaceService

logger = structlog.get_logger(__name__)


async def inspect_excel_file_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    Excelファイルの構造を確認

    Args:
        args:
            file_path: ファイルパス
    """
    file_path = args.get("file_path", "")

    try:
        from openpyxl import load_workbook
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: openpyxlライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        # Excelファイルを読み込み
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)

        result_lines = [
            f"# Excel構造: {filename}",
            f"ファイルサイズ: {len(content) / 1024:.1f} KB",
            f"シート数: {len(wb.sheetnames)}",
            "",
            "## シート一覧",
        ]

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            # 行数・列数を取得
            max_row = ws.max_row or 0
            max_col = ws.max_column or 0

            result_lines.append(f"\n### {sheet_name}")
            result_lines.append(f"- 行数: {max_row}")
            result_lines.append(f"- 列数: {max_col}")

            # ヘッダー行（1行目）を取得
            if max_row > 0 and max_col > 0:
                headers = []
                for col in range(1, min(max_col + 1, 20)):  # 最大20列まで
                    cell_value = ws.cell(row=1, column=col).value
                    headers.append(str(cell_value) if cell_value is not None else "")

                if headers:
                    result_lines.append(f"- ヘッダー: {', '.join(headers)}")

                # データサンプル（2-4行目）
                if max_row > 1:
                    result_lines.append("- サンプルデータ（2-4行目）:")
                    for row_num in range(2, min(max_row + 1, 5)):
                        row_values = []
                        for col in range(1, min(max_col + 1, 10)):  # 最大10列まで
                            cell_value = ws.cell(row=row_num, column=col).value
                            row_values.append(str(cell_value) if cell_value is not None else "")
                        result_lines.append(f"  行{row_num}: {', '.join(row_values)}")

        wb.close()

        result_text = "\n".join(result_lines)
        result_text += "\n\n---\n"
        result_text += "詳細なデータを取得するには `read_excel_sheet` を使用してください。"

        return {
            "content": [{"type": "text", "text": result_text}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("Excel構造確認エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


async def read_excel_sheet_handler(
    workspace_service: "WorkspaceService",
    tenant_id: str,
    conversation_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    Excelシートのデータを取得

    Args:
        args:
            file_path: ファイルパス
            sheet_name: シート名（省略時は最初のシート）
            start_row: 開始行（デフォルト: 1）
            end_row: 終了行（デフォルト: 100）
            columns: 取得する列（例: "A:D" または "A,C,E"）
    """
    file_path = args.get("file_path", "")
    sheet_name = args.get("sheet_name")
    start_row = args.get("start_row", 1)
    end_row = args.get("end_row", 100)
    columns_spec = args.get("columns")

    try:
        from openpyxl import load_workbook
        from openpyxl.utils import column_index_from_string, get_column_letter
    except ImportError:
        return {
            "content": [{"type": "text", "text": "エラー: openpyxlライブラリがインストールされていません。"}],
            "is_error": True,
        }

    try:
        content, filename, content_type = await workspace_service.download_file(
            tenant_id, conversation_id, file_path
        )

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)

        # シートを選択
        if sheet_name:
            if sheet_name not in wb.sheetnames:
                wb.close()
                return {
                    "content": [{
                        "type": "text",
                        "text": f"シート '{sheet_name}' が見つかりません。\n"
                                f"利用可能なシート: {', '.join(wb.sheetnames)}",
                    }],
                    "is_error": True,
                }
            ws = wb[sheet_name]
        else:
            ws = wb.active
            sheet_name = ws.title

        max_row = ws.max_row or 0
        max_col = ws.max_column or 0

        # 列の範囲を解析
        col_indices = _parse_columns(columns_spec, max_col)

        # 行の範囲を調整
        start_row = max(1, start_row)
        end_row = min(end_row, max_row)

        result_lines = [
            f"# {sheet_name} のデータ",
            f"取得範囲: 行 {start_row}-{end_row}",
            "",
        ]

        # ヘッダー行
        headers = []
        for col_idx in col_indices:
            cell_value = ws.cell(row=1, column=col_idx).value
            headers.append(str(cell_value) if cell_value is not None else get_column_letter(col_idx))

        # TSV形式でデータを出力
        result_lines.append("\t".join(headers))
        result_lines.append("-" * 40)

        for row_num in range(start_row, end_row + 1):
            if row_num == 1:  # ヘッダーはすでに出力済み
                continue

            row_values = []
            for col_idx in col_indices:
                cell_value = ws.cell(row=row_num, column=col_idx).value
                row_values.append(str(cell_value) if cell_value is not None else "")

            result_lines.append("\t".join(row_values))

        wb.close()

        result_text = "\n".join(result_lines)

        # 残りの行数を通知
        remaining = max_row - end_row
        if remaining > 0:
            result_text += f"\n\n---\n残り {remaining} 行あります。"
            result_text += f"\n続きを取得するには `start_row={end_row + 1}` を指定してください。"

        return {
            "content": [{"type": "text", "text": result_text}],
        }
    except FileNotFoundError:
        return {
            "content": [{"type": "text", "text": f"ファイルが見つかりません: {file_path}"}],
            "is_error": True,
        }
    except Exception as e:
        logger.error("Excelデータ取得エラー", error=str(e), file_path=file_path)
        return {
            "content": [{"type": "text", "text": f"読み込みエラー: {str(e)}"}],
            "is_error": True,
        }


def _parse_columns(columns_spec: str | None, max_col: int) -> list[int]:
    """
    列指定を解析

    Args:
        columns_spec: "A:D" または "A,C,E" 形式
        max_col: 最大列数

    Returns:
        列インデックスのリスト（1始まり）
    """
    from openpyxl.utils import column_index_from_string

    if not columns_spec:
        # 全列（最大20列）
        return list(range(1, min(max_col + 1, 21)))

    col_indices = []

    # カンマ区切り
    parts = columns_spec.replace(" ", "").upper().split(",")

    for part in parts:
        if ":" in part:
            # 範囲指定 (A:D)
            start_col, end_col = part.split(":")
            start_idx = column_index_from_string(start_col)
            end_idx = column_index_from_string(end_col)
            col_indices.extend(range(start_idx, end_idx + 1))
        else:
            # 単一列 (A)
            col_indices.append(column_index_from_string(part))

    return sorted(set(col_indices))
