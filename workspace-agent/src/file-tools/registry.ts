/**
 * File tools registry
 *
 * Registers tool handlers for file listing and image reading.
 * Format-specific file reading (PDF, Excel, Word, PowerPoint, Image metadata)
 * is handled by Default Skills (Python scripts in default_skills/).
 *
 * Uses local filesystem (/workspace) instead of workspace_service.
 */

import { readFile, stat } from "node:fs/promises";
import { basename, join, extname, relative } from "node:path";
import { readdir } from "node:fs/promises";
import sharp from "sharp";
import { createLogger } from "../logger.js";
import {
  guessMimeType,
  WORKSPACE_ROOT,
  type ToolResult,
} from "./utils.js";

const logger = createLogger("file-tools-registry");

// =============================================================================
// System Prompt
// =============================================================================

export const FILE_TOOLS_PROMPT = `
## ファイル読み込み

ワークスペースのファイルは以下の手順で読んでください：
1. list_workspace_files でファイル一覧を確認
2. 各ファイル形式に対応するスキルで読み取り
   - PDF / Excel / Word / PowerPoint / 画像メタデータ → 対応するDefault Skillを使用
3. 画像の視覚的確認が必要な場合のみ read_image_file を使用

※ 画像読み込みはコンテキストを消費するため、必要な場合のみ使用
※ テキスト/CSV/JSONファイルは従来のReadツールも使用可能
`;

// =============================================================================
// File Category Classification
// =============================================================================

// MIME type to category mapping
const CATEGORY_MAP: Record<string, Set<string>> = {
  image: new Set([
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
  ]),
  pdf: new Set(["application/pdf"]),
  office: new Set([
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
  ]),
  text: new Set([
    "text/plain",
    "text/csv",
    "text/html",
    "text/css",
    "text/javascript",
    "application/json",
    "application/xml",
    "text/markdown",
    "text/x-python",
    "text/x-java",
  ]),
};

// Extension to category mapping (fallback)
const EXTENSION_CATEGORY_MAP: Record<string, Set<string>> = {
  image: new Set([
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".svg",
  ]),
  pdf: new Set([".pdf"]),
  office: new Set([".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt"]),
  text: new Set([
    ".txt",
    ".csv",
    ".html",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".md",
    ".py",
    ".java",
    ".ts",
    ".tsx",
    ".jsx",
    ".yaml",
    ".yml",
    ".sh",
    ".bash",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
    ".toml",
  ]),
};

/**
 * Determine file category from MIME type and extension.
 */
function getFileCategory(filePath: string, mimeType: string | null): string {
  // Try MIME type first
  if (mimeType) {
    for (const [category, mimeTypes] of Object.entries(CATEGORY_MAP)) {
      if (mimeTypes.has(mimeType)) {
        return category;
      }
    }
  }

  // Fallback to extension
  const ext = extname(filePath).toLowerCase();
  for (const [category, extensions] of Object.entries(EXTENSION_CATEGORY_MAP)) {
    if (extensions.has(ext)) {
      return category;
    }
  }

  return "other";
}

// =============================================================================
// Common Handlers
// =============================================================================

/**
 * Walk a directory recursively, yielding file paths.
 * Skips hidden files and directories.
 */
async function* walkDirectory(dir: string): AsyncGenerator<string> {
  const entries = await readdir(dir, { withFileTypes: true });

  for (const entry of entries) {
    // Skip hidden files/directories
    if (entry.name.startsWith(".")) continue;

    const fullPath = join(dir, entry.name);

    if (entry.isDirectory()) {
      yield* walkDirectory(fullPath);
    } else if (entry.isFile()) {
      yield fullPath;
    }
  }
}

/**
 * list_workspace_files handler - List files in workspace
 */
async function listWorkspaceFilesHandler(
  args: Record<string, unknown>,
): Promise<ToolResult> {
  const filterType = (args.filter_type as string) || "all";

  try {
    const filesInfo: Array<{
      path: string;
      name: string;
      size: number;
      type: string;
      mimeType: string;
    }> = [];

    for await (const fullPath of walkDirectory(WORKSPACE_ROOT)) {
      try {
        const fileStat = await stat(fullPath);
        const fileSize = fileStat.size;
        const relPath = relative(WORKSPACE_ROOT, fullPath);
        const fileName = basename(fullPath);
        const mimeType = guessMimeType(fullPath);
        const category = getFileCategory(relPath, mimeType);

        if (filterType !== "all" && category !== filterType) {
          continue;
        }

        filesInfo.push({
          path: relPath,
          name: fileName,
          size: fileSize,
          type: category,
          mimeType: mimeType || "application/octet-stream",
        });
      } catch {
        // Skip files that can't be stat'd
        continue;
      }
    }

    // Format as text
    let resultText = `ワークスペース内のファイル一覧（${filesInfo.length}件）:\n\n`;
    for (const f of filesInfo) {
      const sizeKb = f.size / 1024;
      resultText += `- ${f.path} (${f.type}, ${sizeKb.toFixed(1)}KB)\n`;
    }

    if (filesInfo.length === 0) {
      resultText = "ワークスペースにファイルがありません。";
    }

    return {
      content: [{ type: "text", text: resultText }],
    };
  } catch (e) {
    logger.error({ err: e }, "ファイル一覧取得エラー");
    return {
      content: [
        {
          type: "text",
          text: `エラー: ${e instanceof Error ? e.message : String(e)}`,
        },
      ],
      is_error: true,
    };
  }
}

/**
 * Resize image if needed, preserving aspect ratio.
 * Returns [resizedBuffer, contentType].
 */
async function resizeImageIfNeeded(
  content: Buffer,
  contentType: string | null,
  maxDimension: number,
): Promise<[Buffer, string]> {
  try {
    const image = sharp(content);
    const metadata = await image.metadata();

    const width = metadata.width || 0;
    const height = metadata.height || 0;

    // Check if resize is needed
    if (width <= maxDimension && height <= maxDimension) {
      return [content, contentType || "image/png"];
    }

    // Determine output format
    let outputContentType = "image/png";
    let sharpInstance = image.resize({
      width: width > height ? maxDimension : undefined,
      height: height >= width ? maxDimension : undefined,
      fit: "inside",
      withoutEnlargement: true,
    });

    if (contentType === "image/jpeg" || contentType === "image/jpg") {
      outputContentType = "image/jpeg";
      sharpInstance = sharpInstance.jpeg({ quality: 85 });
    } else {
      sharpInstance = sharpInstance.png();
    }

    const resized = await sharpInstance.toBuffer();
    return [resized, outputContentType];
  } catch (e) {
    logger.warn({ err: e }, "画像リサイズに失敗");
    return [content, contentType || "image/png"];
  }
}

/**
 * read_image_file handler - Read image file visually
 */
async function readImageFileHandler(
  args: Record<string, unknown>,
): Promise<ToolResult> {
  const filePath = (args.file_path as string) || "";
  const maxDimension = (args.max_dimension as number) || 1920;

  try {
    // Read from local filesystem
    const fullPath = join(WORKSPACE_ROOT, filePath);
    const content = await readFile(fullPath);
    const filename = basename(fullPath);
    const contentType = guessMimeType(fullPath);

    // Check if it's an image
    const category = getFileCategory(filename, contentType);
    if (category !== "image") {
      return {
        content: [
          {
            type: "text",
            text:
              `このファイルは画像ではありません: ${filename} (${contentType})\n` +
              "画像ファイル（JPEG/PNG/GIF/WebP）を指定してください。",
          },
        ],
        is_error: true,
      };
    }

    // Resize if needed
    const [resizedContent, finalContentType] = await resizeImageIfNeeded(
      content,
      contentType,
      maxDimension,
    );

    // Base64 encode
    const base64Data = resizedContent.toString("base64");

    // Return as image content block
    return {
      content: [
        {
          type: "image",
          source: {
            type: "base64",
            media_type: finalContentType,
            data: base64Data,
          },
        },
      ],
    };
  } catch (e) {
    if (
      e instanceof Error &&
      "code" in e &&
      (e as NodeJS.ErrnoException).code === "ENOENT"
    ) {
      return {
        content: [
          {
            type: "text",
            text: `ファイルが見つかりません: ${filePath}`,
          },
        ],
        is_error: true,
      };
    }
    logger.error({ err: e, filePath }, "画像ファイル読み込みエラー");
    return {
      content: [
        {
          type: "text",
          text: `読み込みエラー: ${e instanceof Error ? e.message : String(e)}`,
        },
      ],
      is_error: true,
    };
  }
}

// =============================================================================
// Handler Registration
// =============================================================================

/**
 * Create file tool handlers map.
 * Returns a Record<string, handler> where each handler is
 * async (args: Record<string, unknown>) => Promise<ToolResult>
 */
export function createFileToolHandlers(): Record<
  string,
  (args: Record<string, unknown>) => Promise<ToolResult>
> {
  return {
    list_workspace_files: listWorkspaceFilesHandler,
    read_image_file: readImageFileHandler,
  };
}
