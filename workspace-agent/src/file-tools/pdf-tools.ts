/**
 * PDF file tools
 *
 * inspect_pdf_file: Structure inspection (page count, TOC)
 * read_pdf_pages: Text extraction
 * convert_pdf_to_images: Convert pages to images (not supported in Node.js)
 */

import { getDocument, type PDFDocumentProxy } from "pdfjs-dist/legacy/build/pdf.mjs";
import { createLogger } from "../logger.js";
import {
  localFileToolHandler,
  formatToolSuccess,
  formatToolError,
  type ToolResult,
} from "./utils.js";

const logger = createLogger("pdf-tools");

// =============================================================================
// Internal Utilities
// =============================================================================

/**
 * Parse page specification string.
 * Supports "1-5" range and "1,3,5" comma-separated formats.
 * Returns sorted unique page numbers (1-based).
 */
function parsePages(pagesSpec: string, totalPages: number): number[] {
  const pageNumbers: number[] = [];
  const parts = pagesSpec.replace(/\s/g, "").split(",");

  for (const part of parts) {
    if (part.includes("-")) {
      try {
        const [startStr, endStr] = part.split("-");
        const startNum = parseInt(startStr, 10);
        const endNum = parseInt(endStr, 10);
        if (isNaN(startNum) || isNaN(endNum)) continue;
        const clampedEnd = Math.min(endNum, totalPages);
        for (let i = startNum; i <= clampedEnd; i++) {
          pageNumbers.push(i);
        }
      } catch {
        continue;
      }
    } else {
      const num = parseInt(part, 10);
      if (!isNaN(num)) {
        pageNumbers.push(num);
      }
    }
  }

  // Sort and deduplicate
  return [...new Set(pageNumbers)].sort((a, b) => a - b);
}

/**
 * Load a PDF document from a Buffer using pdfjs-dist.
 */
async function loadPdf(content: Buffer): Promise<PDFDocumentProxy> {
  const data = new Uint8Array(content);
  return await getDocument({ data, useSystemFonts: true }).promise;
}

/**
 * Extract text from a single PDF page.
 */
async function extractPageText(
  doc: PDFDocumentProxy,
  pageNum: number,
): Promise<string> {
  const page = await doc.getPage(pageNum);
  const textContent = await page.getTextContent();
  const lines: string[] = [];
  let lastY: number | null = null;

  for (const item of textContent.items) {
    if ("str" in item) {
      const y = (item as { transform: number[] }).transform[5];
      if (lastY !== null && Math.abs(y - lastY) > 2) {
        lines.push("\n");
      }
      lines.push(item.str);
      lastY = y;
    }
  }

  return lines.join("");
}

// =============================================================================
// Tool Handlers
// =============================================================================

/**
 * inspect_pdf_file handler - Inspect PDF structure
 */
export const inspectPdfFileHandler = localFileToolHandler(
  { logPrefix: "PDF構造確認" },
  async ({ content, filename }): Promise<ToolResult> => {
    const doc = await loadPdf(content);
    const numPages = doc.numPages;

    const resultLines: string[] = [
      `# PDF構造: ${filename}`,
      `ファイルサイズ: ${(content.length / 1024).toFixed(1)} KB`,
      `ページ数: ${numPages}`,
      "",
    ];

    // Metadata
    try {
      const metadata = await doc.getMetadata();
      const info = metadata?.info as Record<string, unknown> | undefined;
      if (info) {
        resultLines.push("## メタデータ");
        if (info.Title) {
          resultLines.push(`- タイトル: ${info.Title}`);
        }
        if (info.Author) {
          resultLines.push(`- 作成者: ${info.Author}`);
        }
        if (info.Subject) {
          resultLines.push(`- 件名: ${info.Subject}`);
        }
        resultLines.push("");
      }
    } catch {
      // Metadata not available
    }

    // Table of Contents (outline)
    try {
      const outline = await doc.getOutline();
      if (outline && outline.length > 0) {
        resultLines.push("## 目次");
        const maxItems = 30;
        let count = 0;

        const processOutline = (
          items: Array<{ title: string; items?: unknown[]; dest?: unknown }>,
          level: number,
        ) => {
          for (const item of items) {
            if (count >= maxItems) break;
            const indent = "  ".repeat(level);
            resultLines.push(`${indent}- ${item.title}`);
            count++;
            if (
              item.items &&
              Array.isArray(item.items) &&
              item.items.length > 0
            ) {
              processOutline(
                item.items as Array<{
                  title: string;
                  items?: unknown[];
                  dest?: unknown;
                }>,
                level + 1,
              );
            }
          }
        };

        processOutline(outline as Array<{ title: string; items?: unknown[]; dest?: unknown }>, 0);

        if (outline.length > maxItems) {
          resultLines.push(`  ... 他 ${outline.length - maxItems} 項目`);
        }
        resultLines.push("");
      }
    } catch {
      // Outline not available
    }

    // Page summaries (first 10 pages)
    resultLines.push("## ページ概要");
    const maxPreviewPages = Math.min(10, numPages);

    for (let i = 1; i <= maxPreviewPages; i++) {
      try {
        const text = await extractPageText(doc, i);
        const textLength = text.length;

        // Estimate page type
        let pageType = "テキスト主体";
        if (textLength < 200) {
          pageType = "図表主体";
        }

        // Text preview
        let preview = text.slice(0, 100).replace(/\n/g, " ").trim();
        if (text.length > 100) {
          preview += "...";
        }

        resultLines.push(`\n### ページ ${i}`);
        resultLines.push(`- 種類: ${pageType}`);
        resultLines.push(`- 文字数: ${textLength}`);
        if (preview) {
          resultLines.push(`- プレビュー: ${preview}`);
        }
      } catch {
        resultLines.push(`\n### ページ ${i}`);
        resultLines.push("- [テキスト抽出エラー]");
      }
    }

    if (numPages > 10) {
      resultLines.push(`... 他 ${numPages - 10} ページ`);
    }

    await doc.destroy();

    let resultText = resultLines.join("\n");
    resultText += "\n\n---\n";
    resultText +=
      "テキストを取得するには `read_pdf_pages` を使用してください。\n";
    resultText +=
      "図表を確認するには `convert_pdf_to_images` で画像化してください。";

    return formatToolSuccess(resultText);
  },
);

/**
 * read_pdf_pages handler - Extract text from PDF pages
 */
export const readPdfPagesHandler = localFileToolHandler(
  { logPrefix: "PDFテキスト抽出" },
  async ({ content, filename, args }): Promise<ToolResult> => {
    const pagesSpec = (args.pages as string) || "1-10";

    const doc = await loadPdf(content);
    const totalPages = doc.numPages;

    // Parse page numbers
    const pageNumbers = parsePages(pagesSpec, totalPages);

    const resultLines: string[] = [
      `# ${filename} のテキスト`,
      `取得ページ: ${pagesSpec} (全${totalPages}ページ中)`,
      "",
    ];

    for (const pageNum of pageNumbers) {
      if (pageNum < 1 || pageNum > totalPages) {
        continue;
      }

      resultLines.push(`## ページ ${pageNum}`);
      resultLines.push("");

      try {
        const text = await extractPageText(doc, pageNum);

        if (text.trim()) {
          resultLines.push(text.trim());
        } else {
          resultLines.push(
            "[このページは図表主体です。テキストは抽出できませんでした。]",
          );
          resultLines.push(
            "[内容を確認するには `convert_pdf_to_images` で画像化してください。]",
          );
        }
      } catch {
        resultLines.push("[このページのテキスト抽出に失敗しました。]");
      }

      resultLines.push("");
      resultLines.push("---");
      resultLines.push("");
    }

    await doc.destroy();

    return formatToolSuccess(resultLines.join("\n"));
  },
);

/**
 * convert_pdf_to_images handler - Convert PDF pages to images
 * Note: PDF to image conversion is not supported in Node.js without canvas.
 */
export const convertPdfToImagesHandler = localFileToolHandler(
  { logPrefix: "PDF画像変換" },
  async ({ content, filename, args }): Promise<ToolResult> => {
    const pagesSpec = (args.pages as string) || "1";

    // Load PDF to validate and get page count
    const doc = await loadPdf(content);
    const totalPages = doc.numPages;
    const pageNumbers = parsePages(pagesSpec, totalPages);
    await doc.destroy();

    // Limit to 5 pages
    const limitedPages = pageNumbers.slice(0, 5);
    if (pageNumbers.length > 5) {
      logger.warn(
        { requested: pagesSpec },
        "画像変換を5ページに制限",
      );
    }

    return formatToolError(
      `PDF to image conversion is not supported in this environment.\n\n` +
        `ファイル: ${filename}\n` +
        `リクエストページ: ${limitedPages.join(", ")} (全${totalPages}ページ)\n\n` +
        `代替手段:\n` +
        `- テキスト抽出には \`read_pdf_pages\` を使用してください。\n` +
        `- 図表の内容はテキストベースで確認してください。`,
    );
  },
);
