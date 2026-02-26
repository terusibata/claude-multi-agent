/**
 * File tools common utilities
 *
 * Shared utility functions for Office format tools (Word/Excel/PowerPoint).
 * Handles text normalization, response generation, old format checks, etc.
 */

import { readFile } from "node:fs/promises";
import { basename, extname, join } from "node:path";
import { createLogger } from "../logger.js";

const logger = createLogger("file-tools-utils");

// =============================================================================
// Types
// =============================================================================

export interface ToolResult {
  content: Array<{ type: string; text?: string; source?: object }>;
  is_error?: boolean;
}

// =============================================================================
// MIME Type Mapping
// =============================================================================

const EXTENSION_MIME_MAP: Record<string, string> = {
  // Images
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".bmp": "image/bmp",
  ".tiff": "image/tiff",
  ".tif": "image/tiff",
  ".svg": "image/svg+xml",
  // PDF
  ".pdf": "application/pdf",
  // Office
  ".xlsx":
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".xls": "application/vnd.ms-excel",
  ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".doc": "application/msword",
  ".pptx":
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".ppt": "application/vnd.ms-powerpoint",
  // Text
  ".txt": "text/plain",
  ".csv": "text/csv",
  ".html": "text/html",
  ".css": "text/css",
  ".js": "text/javascript",
  ".json": "application/json",
  ".xml": "application/xml",
  ".md": "text/markdown",
  ".py": "text/x-python",
  ".java": "text/x-java",
  ".ts": "text/typescript",
  ".tsx": "text/typescript",
  ".jsx": "text/javascript",
  ".yaml": "application/yaml",
  ".yml": "application/yaml",
  ".sh": "text/x-shellscript",
  ".log": "text/plain",
  ".ini": "text/plain",
  ".cfg": "text/plain",
  ".conf": "text/plain",
  ".toml": "text/plain",
};

/**
 * Guess the MIME type of a file path based on extension.
 */
export function guessMimeType(filePath: string): string | null {
  const ext = extname(filePath).toLowerCase();
  return EXTENSION_MIME_MAP[ext] || null;
}

// =============================================================================
// Text Utilities
// =============================================================================

/**
 * Normalize text:
 * - Unicode NFC normalization
 * - Remove control characters (preserve newline/tab)
 * - Remove zero-width characters and BOM
 */
export function normalizeText(text: string): string {
  if (!text) {
    return "";
  }

  // Unicode NFC normalization
  text = text.normalize("NFC");

  const result: string[] = [];
  for (const ch of text) {
    const code = ch.codePointAt(0)!;
    if (ch === "\n" || ch === "\r" || ch === "\t") {
      result.push(ch);
    } else if (code < 0x20 || code === 0x7f || (0x80 <= code && code <= 0x9f)) {
      // Remove control characters
      continue;
    } else if (
      code === 0x200b ||
      code === 0x200c ||
      code === 0x200d ||
      code === 0x2060 ||
      code === 0xfeff
    ) {
      // Remove zero-width characters and BOM
      continue;
    } else {
      result.push(ch);
    }
  }

  return result.join("");
}

/**
 * Generate context snippet around a search match.
 * Wraps the match in [brackets] with contextChars characters on each side.
 */
export function createContextSnippet(
  text: string,
  matchStart: number,
  matchEnd: number,
  contextChars: number = 40,
): string {
  const start = Math.max(0, matchStart - contextChars);
  const end = Math.min(text.length, matchEnd + contextChars);

  const prefix = start > 0 ? "..." : "";
  const suffix = end < text.length ? "..." : "";

  const before = text.slice(start, matchStart);
  const match = text.slice(matchStart, matchEnd);
  const after = text.slice(matchEnd, end);

  return `${prefix}${before}[${match}]${after}${suffix}`;
}

/**
 * Check if a file is an old Office format.
 * Returns an error ToolResult if old format, null otherwise.
 */
export function checkOldFormat(
  filePath: string,
  oldExtension: string,
  formatName: string,
  newExtension: string,
  libraryName: string,
  applicationName: string,
): ToolResult | null {
  if (filePath.toLowerCase().endsWith(oldExtension)) {
    return formatToolError(
      `エラー: '${filePath}' は古い${formatName}形式（${oldExtension}）です。\n\n` +
        `このツールは ${newExtension}（Office Open XML）形式のみ対応しています。\n` +
        `${oldExtension}（バイナリ形式）ファイルは ${libraryName} では読み取れません。\n\n` +
        `対処方法:\n` +
        `1. ${applicationName} で ${newExtension} 形式に変換して再アップロード\n` +
        `2. LibreOffice で ${newExtension} 形式に変換して再アップロード\n` +
        `3. オンライン変換ツールを使用`,
    );
  }
  return null;
}

/**
 * Build a search regex pattern from a query string.
 * The query is automatically escaped for regex special characters.
 */
export function buildSearchPattern(
  query: string,
  caseSensitive: boolean = false,
): RegExp {
  const flags = caseSensitive ? "g" : "gi";
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  try {
    return new RegExp(escaped, flags);
  } catch {
    throw new Error(`無効な検索クエリ: ${query}`);
  }
}

// =============================================================================
// Response Helpers
// =============================================================================

/** Generate a tool error response */
export function formatToolError(message: string): ToolResult {
  return {
    content: [{ type: "text", text: message }],
    is_error: true,
  };
}

/** Generate a tool success response */
export function formatToolSuccess(text: string): ToolResult {
  return {
    content: [{ type: "text", text }],
  };
}

// =============================================================================
// Workspace Root
// =============================================================================

export const WORKSPACE_ROOT = "/workspace";

// =============================================================================
// Handler Decorator (local file tool handler)
// =============================================================================

export interface LocalFileToolHandlerOptions {
  /** Old format check: [oldExt, formatName, newExt, libraryName, appName] */
  oldFormat?: [string, string, string, string, string];
  /** Log prefix for error messages */
  logPrefix?: string;
}

/**
 * Wraps a file tool handler with common pre-processing:
 * - Old format check
 * - Local file reading from /workspace
 * - Error handling
 *
 * The wrapped function receives: { content, filename, contentType, args }
 */
export function localFileToolHandler(
  options: LocalFileToolHandlerOptions,
  handler: (params: {
    content: Buffer;
    filename: string;
    contentType: string | null;
    args: Record<string, unknown>;
  }) => Promise<ToolResult>,
): (args: Record<string, unknown>) => Promise<ToolResult> {
  const { oldFormat, logPrefix = "ファイルツール" } = options;

  return async (args: Record<string, unknown>): Promise<ToolResult> => {
    const filePath = (args.file_path as string) || "";

    // Old format check
    if (oldFormat) {
      const err = checkOldFormat(filePath, ...oldFormat);
      if (err) {
        return err;
      }
    }

    try {
      // Read from local filesystem
      const fullPath = join(WORKSPACE_ROOT, filePath);
      const content = await readFile(fullPath);
      const filename = basename(fullPath);
      const contentType = guessMimeType(fullPath);

      return await handler({
        content,
        filename,
        contentType,
        args,
      });
    } catch (e: unknown) {
      if (
        e instanceof Error &&
        "code" in e &&
        (e as NodeJS.ErrnoException).code === "ENOENT"
      ) {
        return formatToolError(`ファイルが見つかりません: ${filePath}`);
      }
      if (e instanceof Error && e.message.startsWith("エラー:")) {
        return formatToolError(e.message);
      }
      const errorMessage = e instanceof Error ? e.message : String(e);
      logger.error({ err: e, filePath }, `${logPrefix}エラー`);
      return formatToolError(`読み込みエラー: ${errorMessage}`);
    }
  };
}
