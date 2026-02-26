/**
 * Excel file tools
 *
 * AI agent tools for understanding Excel files:
 * 1. get_sheet_info: Get sheet list and basic info
 * 2. get_sheet_csv: Get sheet content as CSV Markdown (with row range)
 * 3. search_workbook: Search keywords across the workbook
 */

import ExcelJS from "exceljs";
import { createLogger } from "../logger.js";
import {
  buildSearchPattern,
  localFileToolHandler,
  formatToolError,
  formatToolSuccess,
  normalizeText,
  type ToolResult,
} from "./utils.js";

const logger = createLogger("excel-tools");

// =============================================================================
// Types
// =============================================================================

interface SheetInfo {
  name: string;
  rows: number;
  cols: number;
  range: string;
  hasPrintArea: boolean;
}

interface WorkbookInfo {
  filename: string;
  sheetCount: number;
  sheets: SheetInfo[];
}

interface SheetCSVResult {
  sheetName: string;
  range: string;
  totalRows: number;
  totalCols: number;
  returnedRows: number;
  startRow: number;
  endRow: number;
  hasMore: boolean;
  csvMarkdown: string;
}

interface SearchHit {
  sheet: string;
  cell: string;
  row: number;
  col: number;
  value: string;
  context: string;
}

interface SearchResult {
  query: string;
  totalHits: number;
  hits: SearchHit[];
}

// =============================================================================
// Constants
// =============================================================================

const DEFAULT_MAX_ROWS = 100;

// =============================================================================
// Internal Utilities
// =============================================================================

/**
 * Get cell display value as string.
 */
function getCellValue(cell: ExcelJS.Cell): string {
  const value = cell.value;

  if (value === null || value === undefined) {
    return "";
  }

  // Handle date values
  if (value instanceof Date) {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  // Handle rich text
  if (typeof value === "object" && "richText" in value) {
    const richText = value as { richText: Array<{ text: string }> };
    const text = richText.richText.map((r) => r.text).join("");
    return normalizeText(text).trim();
  }

  // Handle formula results
  if (typeof value === "object" && "result" in value) {
    const formula = value as { result?: unknown };
    if (formula.result instanceof Date) {
      const d = formula.result;
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    }
    if (formula.result !== undefined && formula.result !== null) {
      return normalizeText(String(formula.result)).trim();
    }
    return "";
  }

  // Handle hyperlinks
  if (typeof value === "object" && "text" in value) {
    return normalizeText(String((value as { text: string }).text)).trim();
  }

  const text = String(value);
  return normalizeText(text).trim();
}

/**
 * Convert column number (1-based) to Excel column letter (A, B, ..., Z, AA, ...).
 */
function getColumnLetter(col: number): string {
  let result = "";
  let n = col;
  while (n > 0) {
    n--;
    result = String.fromCharCode(65 + (n % 26)) + result;
    n = Math.floor(n / 26);
  }
  return result;
}

/**
 * Convert coordinates to Excel range string (e.g., A1:D10).
 */
function coordsToRangeString(
  minRow: number,
  minCol: number,
  maxRow: number,
  maxCol: number,
): string {
  const start = `${getColumnLetter(minCol)}${minRow}`;
  const end = `${getColumnLetter(maxCol)}${maxRow}`;
  return `${start}:${end}`;
}

/**
 * Load workbook from buffer.
 */
async function loadWorkbookFromBuffer(
  content: Buffer,
): Promise<ExcelJS.Workbook> {
  const workbook = new ExcelJS.Workbook();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await workbook.xlsx.load(content as any);
  return workbook;
}

/**
 * Get used range of a worksheet.
 * Returns [minRow, minCol, maxRow, maxCol].
 */
function getUsedRange(
  ws: ExcelJS.Worksheet,
): [number, number, number, number] {
  // ExcelJS tracks actual dimensions
  const rowCount = ws.rowCount || 1;
  const colCount = ws.columnCount || 1;

  // Find actual data bounds by scanning
  let minRow = 1;
  let minCol = 1;
  let maxRow = rowCount;
  let maxCol = colCount;

  // If worksheet has dimensions, use them
  if (ws.dimensions) {
    const dims = ws.dimensions;
    // ExcelJS Worksheet dimensions model
    const model = dims as unknown as {
      top: number;
      left: number;
      bottom: number;
      right: number;
    };
    if (model.top && model.left && model.bottom && model.right) {
      minRow = model.top;
      minCol = model.left;
      maxRow = model.bottom;
      maxCol = model.right;
    }
  }

  return [
    Math.max(1, minRow),
    Math.max(1, minCol),
    Math.max(1, maxRow),
    Math.max(1, maxCol),
  ];
}

/**
 * Build merged cell lookup: maps (row, col) -> (topLeftRow, topLeftCol).
 */
function buildMergedLookup(
  ws: ExcelJS.Worksheet,
  area: [number, number, number, number],
): Map<string, string> {
  const lookup = new Map<string, string>();
  const [areaMinRow, areaMinCol, areaMaxRow, areaMaxCol] = area;

  // ExcelJS stores merged cells as an array of range strings
  const mergedCells = (ws as unknown as { _merges?: Record<string, unknown> })
    ._merges;
  if (!mergedCells) return lookup;

  for (const key of Object.keys(mergedCells)) {
    try {
      const merge = mergedCells[key] as {
        top: number;
        left: number;
        bottom: number;
        right: number;
      };
      const r0 = merge.top;
      const c0 = merge.left;
      const r1 = merge.bottom;
      const c1 = merge.right;

      if (r1 < areaMinRow || r0 > areaMaxRow) continue;
      if (c1 < areaMinCol || c0 > areaMaxCol) continue;

      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          if (
            areaMinRow <= r &&
            r <= areaMaxRow &&
            areaMinCol <= c &&
            c <= areaMaxCol
          ) {
            lookup.set(`${r},${c}`, `${r0},${c0}`);
          }
        }
      }
    } catch {
      continue;
    }
  }

  return lookup;
}

/**
 * Escape a field for CSV output (RFC 4180).
 */
function csvEscape(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

// =============================================================================
// Core Functions
// =============================================================================

/**
 * Get sheet information from an Excel file.
 */
async function getSheetInfo(
  content: Buffer,
  filename: string,
): Promise<WorkbookInfo> {
  const wb = await loadWorkbookFromBuffer(content);

  const sheets: SheetInfo[] = [];

  wb.eachSheet((ws: ExcelJS.Worksheet) => {
    const [minRow, minCol, maxRow, maxCol] = getUsedRange(ws);
    const rows = maxRow - minRow + 1;
    const cols = maxCol - minCol + 1;
    const rangeStr = coordsToRangeString(minRow, minCol, maxRow, maxCol);

    sheets.push({
      name: ws.name,
      rows,
      cols,
      range: rangeStr,
      hasPrintArea: false, // ExcelJS doesn't easily expose print area
    });
  });

  return {
    filename,
    sheetCount: sheets.length,
    sheets,
  };
}

/**
 * Get sheet content as CSV Markdown.
 */
async function getSheetCsv(
  content: Buffer,
  sheetName: string,
  options: {
    startRow?: number | null;
    endRow?: number | null;
    maxRows?: number;
    usePrintArea?: boolean;
  } = {},
): Promise<SheetCSVResult> {
  const { startRow = null, endRow = null, maxRows = DEFAULT_MAX_ROWS } = options;

  const wb = await loadWorkbookFromBuffer(content);
  const ws = wb.getWorksheet(sheetName);

  if (!ws) {
    throw new Error(`シートが見つかりません: ${sheetName}`);
  }

  const [areaMinRow, areaMinCol, areaMaxRow, areaMaxCol] = getUsedRange(ws);

  const totalRows = areaMaxRow - areaMinRow + 1;
  const totalCols = areaMaxCol - areaMinCol + 1;

  // Determine actual fetch range
  let actualStartRow = startRow !== null ? startRow : areaMinRow;

  let actualEndRow: number;
  if (endRow !== null) {
    actualEndRow = Math.min(endRow, areaMaxRow);
  } else {
    actualEndRow = Math.min(actualStartRow + maxRows - 1, areaMaxRow);
  }

  // Clamp to valid range
  actualStartRow = Math.max(actualStartRow, areaMinRow);
  actualEndRow = Math.min(actualEndRow, areaMaxRow);

  const area: [number, number, number, number] = [
    actualStartRow,
    areaMinCol,
    actualEndRow,
    areaMaxCol,
  ];

  // Build merged cell lookup
  const mergedLookup = buildMergedLookup(ws, area);

  // Extract CSV data
  const csvRows: string[][] = [];

  for (let r = actualStartRow; r <= actualEndRow; r++) {
    const rowValues: string[] = [];
    const row = ws.getRow(r);

    for (let c = areaMinCol; c <= areaMaxCol; c++) {
      const cell = row.getCell(c);
      const topLeft = mergedLookup.get(`${r},${c}`);

      let text: string;
      if (topLeft) {
        if (`${r},${c}` === topLeft) {
          text = getCellValue(cell);
        } else {
          text = "";
        }
      } else {
        text = getCellValue(cell);
      }

      // Replace newlines in cell values
      text = text.replace(/\r\n/g, " ").replace(/\n/g, " ").replace(/\r/g, " ");
      rowValues.push(text);
    }

    csvRows.push(rowValues);
  }

  // Generate CSV string (RFC 4180)
  const csvContent = csvRows
    .map((row) => row.map(csvEscape).join(","))
    .join("\n");

  const csvMarkdown = `\`\`\`csv\n${csvContent}\n\`\`\``;

  const hasMore = actualEndRow < areaMaxRow;

  return {
    sheetName,
    range: coordsToRangeString(
      actualStartRow,
      areaMinCol,
      actualEndRow,
      areaMaxCol,
    ),
    totalRows,
    totalCols,
    returnedRows: csvRows.length,
    startRow: actualStartRow,
    endRow: actualEndRow,
    hasMore,
    csvMarkdown,
  };
}

/**
 * Search the entire workbook for a keyword.
 */
async function searchWorkbook(
  content: Buffer,
  query: string,
  options: {
    caseSensitive?: boolean;
    maxHits?: number;
  } = {},
): Promise<SearchResult> {
  const { caseSensitive = false, maxHits = 50 } = options;

  const wb = await loadWorkbookFromBuffer(content);
  const hits: SearchHit[] = [];

  const pattern = buildSearchPattern(query, caseSensitive);

  wb.eachSheet((ws: ExcelJS.Worksheet) => {
    if (hits.length >= maxHits) return;

    const [minRow, minCol, maxRow, maxCol] = getUsedRange(ws);

    for (let r = minRow; r <= maxRow; r++) {
      if (hits.length >= maxHits) break;

      const row = ws.getRow(r);
      // Cache row values for context generation
      const rowValues: Map<number, string> = new Map();
      for (let c = minCol; c <= maxCol; c++) {
        const cell = row.getCell(c);
        rowValues.set(c, getCellValue(cell));
      }

      for (let c = minCol; c <= maxCol; c++) {
        if (hits.length >= maxHits) break;

        const value = rowValues.get(c) || "";
        if (!value) continue;

        // Reset regex lastIndex for global patterns
        pattern.lastIndex = 0;
        if (pattern.test(value)) {
          // Generate context (2 cells before and after)
          const contextParts: string[] = [];
          for (
            let ctxCol = Math.max(minCol, c - 2);
            ctxCol <= Math.min(maxCol, c + 2);
            ctxCol++
          ) {
            const ctxValue = rowValues.get(ctxCol) || "";
            if (ctxValue) {
              const colLetter = getColumnLetter(ctxCol);
              if (ctxCol === c) {
                contextParts.push(`[${colLetter}:${ctxValue}]`);
              } else {
                contextParts.push(`${colLetter}:${ctxValue}`);
              }
            }
          }

          const context = contextParts.join(" | ");

          hits.push({
            sheet: ws.name,
            cell: `${getColumnLetter(c)}${r}`,
            row: r,
            col: c,
            value,
            context,
          });
        }
      }
    }
  });

  return {
    query,
    totalHits: hits.length,
    hits,
  };
}

// =============================================================================
// Tool Handlers
// =============================================================================

/**
 * get_sheet_info handler
 */
export const getSheetInfoHandler = localFileToolHandler(
  {
    oldFormat: [".xls", "Excel", ".xlsx", "exceljs", "Microsoft Excel"],
    logPrefix: "Excel情報取得",
  },
  async ({ content, filename }): Promise<ToolResult> => {
    const info = await getSheetInfo(content, filename);

    const resultLines: string[] = [
      `# Excel情報: ${info.filename}`,
      `シート数: ${info.sheetCount}`,
      "",
      "## シート一覧",
    ];

    for (const sheet of info.sheets) {
      resultLines.push("");
      resultLines.push(`### ${sheet.name}`);
      resultLines.push(
        `- 範囲: ${sheet.range} (${sheet.rows}行 x ${sheet.cols}列)`,
      );
      if (sheet.hasPrintArea) {
        resultLines.push("- 印刷領域: 設定済み");
      }
    }

    resultLines.push("");
    resultLines.push("---");
    resultLines.push(
      "データを取得するには `get_sheet_csv` を使用してください。",
    );
    resultLines.push(
      "キーワード検索には `search_workbook` を使用してください。",
    );

    return formatToolSuccess(resultLines.join("\n"));
  },
);

/**
 * get_sheet_csv handler
 */
export const getSheetCsvHandler = localFileToolHandler(
  {
    oldFormat: [".xls", "Excel", ".xlsx", "exceljs", "Microsoft Excel"],
    logPrefix: "ExcelCSV取得",
  },
  async ({ content, args }): Promise<ToolResult> => {
    const sheetName = (args.sheet_name as string) || "";
    const startRow = args.start_row as number | undefined;
    const endRow = args.end_row as number | undefined;
    const maxRows = (args.max_rows as number) || DEFAULT_MAX_ROWS;
    const usePrintArea = args.use_print_area !== false;

    if (!sheetName) {
      return formatToolError(
        "エラー: sheet_name（シート名）を指定してください。\n" +
          "get_sheet_info でシート一覧を確認できます。",
      );
    }

    const result = await getSheetCsv(content, sheetName, {
      startRow: startRow ?? null,
      endRow: endRow ?? null,
      maxRows,
      usePrintArea,
    });

    const resultLines: string[] = [
      `# ${result.sheetName}`,
      `範囲: ${result.range}`,
      `サイズ: ${result.returnedRows}/${result.totalRows}行 x ${result.totalCols}列`,
      "",
      result.csvMarkdown,
    ];

    if (result.hasMore) {
      resultLines.push("");
      resultLines.push("---");
      resultLines.push("まだ続きがあります。次を取得するには:");
      resultLines.push(
        `\`start_row=${result.endRow + 1}\` を指定してください。`,
      );
    }

    return formatToolSuccess(resultLines.join("\n"));
  },
);

/**
 * search_workbook handler
 */
export const searchWorkbookHandler = localFileToolHandler(
  {
    oldFormat: [".xls", "Excel", ".xlsx", "exceljs", "Microsoft Excel"],
    logPrefix: "Excel検索",
  },
  async ({ content, args }): Promise<ToolResult> => {
    const query = (args.query as string) || "";
    const caseSensitive = (args.case_sensitive as boolean) || false;
    const maxHits = (args.max_hits as number) || 50;

    if (!query) {
      return formatToolError(
        "エラー: query（検索キーワード）を指定してください。",
      );
    }

    const result = await searchWorkbook(content, query, {
      caseSensitive,
      maxHits,
    });

    const resultLines: string[] = [
      `# 検索結果: "${result.query}"`,
      `ヒット数: ${result.totalHits}`,
      "",
    ];

    if (result.hits.length > 0) {
      for (const hit of result.hits) {
        resultLines.push(`## ${hit.sheet}!${hit.cell}`);
        resultLines.push(`値: ${hit.value}`);
        resultLines.push(`コンテキスト: ${hit.context}`);
        resultLines.push("");
      }
    } else {
      resultLines.push("検索結果はありませんでした。");
    }

    return formatToolSuccess(resultLines.join("\n"));
  },
);
