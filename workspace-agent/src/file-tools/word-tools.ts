/**
 * Word file tools
 *
 * AI agent tools for understanding Word files:
 * 1. get_document_info: Get document structure and basic info
 * 2. get_document_content: Get content from specified range (paragraph range)
 * 3. search_document: Search keywords across the document
 *
 * Uses mammoth for .docx parsing.
 */

import mammoth from "mammoth";
import { createLogger } from "../logger.js";
import {
  buildSearchPattern,
  createContextSnippet,
  localFileToolHandler,
  formatToolError,
  formatToolSuccess,
  normalizeText,
  type ToolResult,
} from "./utils.js";

const logger = createLogger("word-tools");

// =============================================================================
// Types
// =============================================================================

interface HeadingInfo {
  level: number;
  text: string;
  paraIndex: number;
  charCount: number;
}

interface TableInfo {
  index: number;
  rows: number;
  cols: number;
  nearPara: number;
}

interface DocumentInfo {
  filename: string;
  totalParagraphs: number;
  totalCharacters: number;
  tablesCount: number;
  headings: HeadingInfo[];
  tables: TableInfo[];
}

interface ContentResult {
  filename: string;
  sectionTitle: string | null;
  startParagraph: number;
  endParagraph: number;
  totalParagraphs: number;
  returnedParagraphs: number;
  hasMore: boolean;
  content: string;
  tablesInRange: number;
}

interface WordSearchHit {
  locationType: "paragraph" | "table" | "heading";
  paraIndex: number | null;
  tableIndex: number | null;
  headingLevel: number | null;
  text: string;
  context: string;
}

interface WordSearchResult {
  query: string;
  totalHits: number;
  hits: WordSearchHit[];
}

// =============================================================================
// Constants
// =============================================================================

const DEFAULT_MAX_PARAGRAPHS = 50;

// =============================================================================
// Internal Utilities
// =============================================================================

/**
 * Parsed paragraph from mammoth HTML output.
 */
interface ParsedParagraph {
  text: string;
  headingLevel: number | null; // null for normal paragraphs
  isTable: boolean;
}

/**
 * Parsed table from mammoth HTML output.
 */
interface ParsedTable {
  rows: string[][];
  index: number;
}

/**
 * Extract structured content from mammoth HTML output.
 * Returns paragraphs with heading detection and table info.
 */
function parseHtmlContent(html: string): {
  paragraphs: ParsedParagraph[];
  tables: ParsedTable[];
} {
  const paragraphs: ParsedParagraph[] = [];
  const tables: ParsedTable[] = [];

  // Extract tables first
  const tableRegex = /<table[^>]*>([\s\S]*?)<\/table>/gi;
  let tableMatch: RegExpExecArray | null;
  let tableIndex = 0;

  while ((tableMatch = tableRegex.exec(html)) !== null) {
    tableIndex++;
    const tableHtml = tableMatch[1];
    const rows: string[][] = [];

    const rowRegex = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
    let rowMatch: RegExpExecArray | null;

    while ((rowMatch = rowRegex.exec(tableHtml)) !== null) {
      const rowHtml = rowMatch[1];
      const cells: string[] = [];

      const cellRegex = /<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi;
      let cellMatch: RegExpExecArray | null;

      while ((cellMatch = cellRegex.exec(rowHtml)) !== null) {
        const cellText = cellMatch[1]
          .replace(/<[^>]+>/g, "")
          .replace(/&lt;/g, "<")
          .replace(/&gt;/g, ">")
          .replace(/&amp;/g, "&")
          .replace(/&quot;/g, '"')
          .replace(/&#39;/g, "'")
          .trim();
        cells.push(cellText);
      }

      if (cells.length > 0) {
        rows.push(cells);
      }
    }

    if (rows.length > 0) {
      tables.push({ rows, index: tableIndex });
    }
  }

  // Remove tables from HTML for paragraph parsing
  const htmlWithoutTables = html.replace(/<table[^>]*>[\s\S]*?<\/table>/gi, "");

  // Parse headings and paragraphs
  const blockRegex =
    /<(h[1-6]|p|li)[^>]*>([\s\S]*?)<\/\1>/gi;
  let blockMatch: RegExpExecArray | null;

  while ((blockMatch = blockRegex.exec(htmlWithoutTables)) !== null) {
    const tag = blockMatch[1].toLowerCase();
    const content = blockMatch[2]
      .replace(/<[^>]+>/g, "")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .trim();

    if (!content) continue;

    let headingLevel: number | null = null;
    if (tag.startsWith("h") && tag.length === 2) {
      headingLevel = parseInt(tag[1], 10);
    }

    paragraphs.push({
      text: content,
      headingLevel,
      isTable: false,
    });
  }

  return { paragraphs, tables };
}

// =============================================================================
// Core Functions
// =============================================================================

/**
 * Get Word document structure info.
 */
async function getDocumentInfo(
  content: Buffer,
  filename: string,
): Promise<DocumentInfo> {
  const result = await mammoth.convertToHtml({ buffer: content });
  const { paragraphs, tables } = parseHtmlContent(result.value);

  const totalParagraphs = paragraphs.length;
  let totalCharacters = 0;

  // Collect heading info
  const headings: HeadingInfo[] = [];
  let currentHeadingChars = 0;

  for (let i = 0; i < paragraphs.length; i++) {
    const para = paragraphs[i];
    const paraText = normalizeText(para.text);
    const charCount = paraText.length;
    totalCharacters += charCount;

    if (para.headingLevel !== null) {
      // Finalize previous heading's char count
      if (headings.length > 0) {
        headings[headings.length - 1].charCount = currentHeadingChars;
      }

      headings.push({
        level: para.headingLevel,
        text: paraText.trim().slice(0, 100),
        paraIndex: i + 1,
        charCount: 0,
      });
      currentHeadingChars = charCount;
    } else {
      currentHeadingChars += charCount;
    }
  }

  // Finalize last heading's char count
  if (headings.length > 0) {
    headings[headings.length - 1].charCount = currentHeadingChars;
  }

  // Table info
  const tableInfos: TableInfo[] = tables.map((t) => ({
    index: t.index,
    rows: t.rows.length,
    cols: t.rows[0]?.length || 0,
    nearPara: Math.min(
      totalParagraphs,
      t.index * Math.floor(totalParagraphs / (tables.length + 1)),
    ),
  }));

  return {
    filename,
    totalParagraphs,
    totalCharacters,
    tablesCount: tables.length,
    headings,
    tables: tableInfos,
  };
}

/**
 * Get document content from specified range.
 */
async function getDocumentContent(
  content: Buffer,
  options: {
    heading?: string | null;
    startParagraph?: number | null;
    endParagraph?: number | null;
    maxParagraphs?: number;
    includeTables?: boolean;
  } = {},
): Promise<ContentResult> {
  const {
    heading = null,
    startParagraph = null,
    endParagraph = null,
    maxParagraphs = DEFAULT_MAX_PARAGRAPHS,
    includeTables = true,
  } = options;

  const result = await mammoth.convertToHtml({ buffer: content });
  const { paragraphs, tables } = parseHtmlContent(result.value);

  const totalParagraphs = paragraphs.length;
  let sectionTitle: string | null = null;
  let actualStart = 1;
  let actualEnd = totalParagraphs;

  if (heading) {
    // Search by heading
    let found = false;
    for (let i = 0; i < paragraphs.length; i++) {
      const para = paragraphs[i];
      if (
        para.headingLevel !== null &&
        para.text.toLowerCase().includes(heading.toLowerCase())
      ) {
        found = true;
        actualStart = i + 1;
        sectionTitle = para.text.trim().slice(0, 100);
        const currentLevel = para.headingLevel;

        // Find next heading at same or higher level
        for (let j = i + 1; j < totalParagraphs; j++) {
          if (
            paragraphs[j].headingLevel !== null &&
            paragraphs[j].headingLevel! <= currentLevel
          ) {
            actualEnd = j;
            break;
          }
        }
        if (!found) {
          actualEnd = totalParagraphs;
        }
        break;
      }
    }

    if (!found) {
      throw new Error(
        `見出し '${heading}' が見つかりません。get_document_info でシート一覧を確認してください。`,
      );
    }
  } else {
    // Specify by paragraph number
    if (startParagraph !== null) {
      actualStart = Math.max(1, startParagraph);
    }
    if (endParagraph !== null) {
      actualEnd = Math.min(totalParagraphs, endParagraph);
    } else {
      actualEnd = Math.min(actualStart + maxParagraphs - 1, totalParagraphs);
    }
  }

  // Limit to max paragraphs
  if (actualEnd - actualStart + 1 > maxParagraphs) {
    actualEnd = actualStart + maxParagraphs - 1;
  }

  // Extract content
  const contentLines: string[] = [];
  let tablesInRange = 0;

  for (
    let i = actualStart - 1;
    i < Math.min(actualEnd, totalParagraphs);
    i++
  ) {
    const para = paragraphs[i];
    const paraText = normalizeText(para.text).trim();
    if (paraText) {
      contentLines.push(paraText);
      contentLines.push("");
    }
  }

  // Table processing
  if (includeTables && tables.length > 0) {
    tablesInRange = tables.length;
    contentLines.push("");
    contentLines.push(
      `[ドキュメントには ${tablesInRange} 個の表が含まれています]`,
    );
  }

  const hasMore = actualEnd < totalParagraphs;

  return {
    filename: "",
    sectionTitle,
    startParagraph: actualStart,
    endParagraph: actualEnd,
    totalParagraphs,
    returnedParagraphs: actualEnd - actualStart + 1,
    hasMore,
    content: contentLines.join("\n"),
    tablesInRange,
  };
}

/**
 * Search the document for a keyword.
 */
async function searchDocument(
  content: Buffer,
  query: string,
  options: {
    caseSensitive?: boolean;
    maxHits?: number;
    includeTables?: boolean;
  } = {},
): Promise<WordSearchResult> {
  const {
    caseSensitive = false,
    maxHits = 50,
    includeTables = true,
  } = options;

  const result = await mammoth.convertToHtml({ buffer: content });
  const { paragraphs, tables } = parseHtmlContent(result.value);

  const hits: WordSearchHit[] = [];
  const pattern = buildSearchPattern(query, caseSensitive);

  // Search paragraphs
  for (let i = 0; i < paragraphs.length; i++) {
    if (hits.length >= maxHits) break;

    const para = paragraphs[i];
    const paraText = normalizeText(para.text);
    if (!paraText) continue;

    pattern.lastIndex = 0;
    const match = pattern.exec(paraText);
    if (match) {
      hits.push({
        locationType: para.headingLevel !== null ? "heading" : "paragraph",
        paraIndex: i + 1,
        tableIndex: null,
        headingLevel: para.headingLevel,
        text:
          paraText.length > 200 ? paraText.slice(0, 200) : paraText,
        context: createContextSnippet(
          paraText,
          match.index,
          match.index + match[0].length,
        ),
      });
    }
  }

  // Search tables
  if (includeTables) {
    for (const table of tables) {
      if (hits.length >= maxHits) break;

      for (let rowIdx = 0; rowIdx < table.rows.length; rowIdx++) {
        if (hits.length >= maxHits) break;

        const row = table.rows[rowIdx];
        const rowTexts: string[] = [];

        let foundInRow = false;
        for (const cellText of row) {
          const normalized = normalizeText(cellText).trim();
          rowTexts.push(normalized);

          if (!foundInRow) {
            pattern.lastIndex = 0;
            const match = pattern.exec(normalized);
            if (match && hits.length < maxHits) {
              const rowContext = rowTexts.join(" | ");
              hits.push({
                locationType: "table",
                paraIndex: null,
                tableIndex: table.index,
                headingLevel: null,
                text:
                  normalized.length > 200
                    ? normalized.slice(0, 200)
                    : normalized,
                context: `表${table.index}, 行${rowIdx + 1}: ${rowContext.slice(0, 150)}`,
              });
              foundInRow = true;
            }
          }
        }
      }
    }
  }

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
 * get_document_info handler
 */
export const getDocumentInfoHandler = localFileToolHandler(
  {
    oldFormat: [".doc", "Word", ".docx", "mammoth", "Microsoft Word"],
    logPrefix: "Word情報取得",
  },
  async ({ content, filename }): Promise<ToolResult> => {
    const info = await getDocumentInfo(content, filename);

    const resultLines: string[] = [
      `# Word文書情報: ${info.filename}`,
      `総段落数: ${info.totalParagraphs}`,
      `総文字数: ${info.totalCharacters.toLocaleString()}`,
      `表の数: ${info.tablesCount}`,
      "",
    ];

    // Heading structure
    if (info.headings.length > 0) {
      resultLines.push("## 見出し構造");
      const maxHeadings = Math.min(50, info.headings.length);
      for (let i = 0; i < maxHeadings; i++) {
        const h = info.headings[i];
        const indent = "  ".repeat(h.level - 1);
        resultLines.push(
          `${indent}- ${h.text} (段落${h.paraIndex}, 約${h.charCount}文字)`,
        );
      }
      if (info.headings.length > 50) {
        resultLines.push(
          `... 他 ${info.headings.length - 50} 見出し`,
        );
      }
      resultLines.push("");
    } else {
      resultLines.push("## 見出し構造");
      resultLines.push("見出しは定義されていません。");
      resultLines.push("");
    }

    // Table summary
    if (info.tables.length > 0) {
      resultLines.push("## 表の概要");
      const maxTables = Math.min(10, info.tables.length);
      for (let i = 0; i < maxTables; i++) {
        const t = info.tables[i];
        resultLines.push(`- 表${t.index}: ${t.rows}行 x ${t.cols}列`);
      }
      if (info.tables.length > 10) {
        resultLines.push(
          `... 他 ${info.tables.length - 10} 表`,
        );
      }
      resultLines.push("");
    }

    resultLines.push("---");
    resultLines.push("データ取得: `get_document_content` を使用");
    resultLines.push("検索: `search_document` を使用");

    return formatToolSuccess(resultLines.join("\n"));
  },
);

/**
 * get_document_content handler
 */
export const getDocumentContentHandler = localFileToolHandler(
  {
    oldFormat: [".doc", "Word", ".docx", "mammoth", "Microsoft Word"],
    logPrefix: "Wordコンテンツ取得",
  },
  async ({ content, filename, args }): Promise<ToolResult> => {
    const heading = (args.heading as string) || undefined;
    const startParagraph = args.start_paragraph as number | undefined;
    const endParagraph = args.end_paragraph as number | undefined;
    const maxParagraphs =
      (args.max_paragraphs as number) || DEFAULT_MAX_PARAGRAPHS;
    const includeTables = args.include_tables !== false;

    const result = await getDocumentContent(content, {
      heading: heading ?? null,
      startParagraph: startParagraph ?? null,
      endParagraph: endParagraph ?? null,
      maxParagraphs,
      includeTables,
    });

    const resultLines: string[] = [`# ${filename}`];

    if (result.sectionTitle) {
      resultLines.push(`セクション: ${result.sectionTitle}`);
    }

    resultLines.push(
      `段落範囲: ${result.startParagraph}-${result.endParagraph} (全${result.totalParagraphs}段落)`,
    );
    resultLines.push(`取得: ${result.returnedParagraphs}段落`);
    resultLines.push("");
    resultLines.push(result.content);

    if (result.hasMore) {
      resultLines.push("");
      resultLines.push("---");
      resultLines.push("まだ続きがあります。次を取得するには:");
      resultLines.push(
        `\`start_paragraph=${result.endParagraph + 1}\` を指定してください。`,
      );
    }

    return formatToolSuccess(resultLines.join("\n"));
  },
);

/**
 * search_document handler
 */
export const searchDocumentHandler = localFileToolHandler(
  {
    oldFormat: [".doc", "Word", ".docx", "mammoth", "Microsoft Word"],
    logPrefix: "Word検索",
  },
  async ({ content, args }): Promise<ToolResult> => {
    const query = (args.query as string) || "";
    const caseSensitive = (args.case_sensitive as boolean) || false;
    const maxHits = (args.max_hits as number) || 50;
    const includeTables = args.include_tables !== false;

    if (!query) {
      return formatToolError(
        "エラー: query（検索キーワード）を指定してください。",
      );
    }

    const result = await searchDocument(content, query, {
      caseSensitive,
      maxHits,
      includeTables,
    });

    const resultLines: string[] = [
      `# 検索結果: "${result.query}"`,
      `ヒット数: ${result.totalHits}`,
      "",
    ];

    if (result.hits.length > 0) {
      for (const hit of result.hits) {
        if (hit.locationType === "heading") {
          resultLines.push(
            `## 見出し (段落${hit.paraIndex}, レベル${hit.headingLevel})`,
          );
        } else if (hit.locationType === "paragraph") {
          resultLines.push(`## 段落 ${hit.paraIndex}`);
        } else {
          resultLines.push(`## 表${hit.tableIndex}`);
        }

        resultLines.push(`コンテキスト: ${hit.context}`);
        resultLines.push("");
      }
    } else {
      resultLines.push("検索結果はありませんでした。");
    }

    return formatToolSuccess(resultLines.join("\n"));
  },
);
