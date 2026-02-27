/**
 * File tools registry
 *
 * Registers tool handlers for file listing and image reading.
 * Format-specific file reading (PDF, Excel, Word, PowerPoint)
 * is handled by Default Skills (Python scripts in default_skills/).
 *
 * Uses local filesystem (/workspace) instead of workspace_service.
 */

import { readFile, stat } from "node:fs/promises";
import { basename, join, extname, relative } from "node:path";
import { readdir } from "node:fs/promises";
import sharp from "sharp";
import type { ImageFormat } from "@aws-sdk/client-bedrock-runtime";
import { createLogger } from "../logger.js";
import {
  guessMimeType,
  WORKSPACE_ROOT,
  type ToolResult,
} from "./utils.js";
import {
  analyzeImagesWithBedrock,
  SUPPORTED_VISION_FORMATS,
  type ImageInput,
} from "./bedrock-vision.js";

const logger = createLogger("file-tools-registry");

// =============================================================================
// System Prompt
// =============================================================================

export const FILE_TOOLS_PROMPT = `
## ファイル読み込み

ワークスペースのファイルは以下の手順で読んでください：
1. list_workspace_files でファイル一覧を確認
2. 各ファイル形式に対応するスキルで読み取り
   - PDF / Excel / Word / PowerPoint → 対応するDefault Skillを使用
3. 画像の内容を理解する必要がある場合は read_image_file を使用（promptパラメータで知りたい内容を指定）
   - 複数画像を一括分析する場合は file_paths パラメータに配列で指定
   - 対応フォーマット: JPEG/PNG/GIF/WebP

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

/** MIME type を Bedrock Converse API の ImageFormat に変換 */
function toImageFormat(mimeType: string): ImageFormat | null {
  const map: Record<string, ImageFormat> = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
  };
  return map[mimeType] || null;
}

/** 最大画像数（Bedrock Converse API の制限に合わせて安全マージン） */
const MAX_IMAGES = 20;

/**
 * read_image_file handler - AI で画像を分析しテキストで返す
 *
 * 単一画像（file_path）または複数画像（file_paths）を受け付け、
 * Bedrock Converse API で分析してテキスト結果を返す。
 */
async function readImageFileHandler(
  args: Record<string, unknown>,
): Promise<ToolResult> {
  const maxDimension = (args.max_dimension as number) || 1568;
  const prompt =
    (args.prompt as string) ||
    "この画像の内容を詳細に説明してください。テキストが含まれる場合は読み取ってください。";

  // file_path / file_paths を正規化
  let filePaths: string[];
  if (args.file_paths && Array.isArray(args.file_paths)) {
    filePaths = args.file_paths as string[];
  } else if (args.file_path && typeof args.file_path === "string") {
    filePaths = [args.file_path];
  } else {
    return {
      content: [
        {
          type: "text",
          text: "file_path（単一）または file_paths（配列）のいずれかを指定してください。",
        },
      ],
      is_error: true,
    };
  }

  if (filePaths.length === 0) {
    return {
      content: [
        { type: "text", text: "画像ファイルのパスが指定されていません。" },
      ],
      is_error: true,
    };
  }

  if (filePaths.length > MAX_IMAGES) {
    return {
      content: [
        {
          type: "text",
          text: `一度に分析できる画像は最大${MAX_IMAGES}枚です（${filePaths.length}枚指定されています）。`,
        },
      ],
      is_error: true,
    };
  }

  try {
    // 全画像を読み込み・バリデーション・リサイズ
    const images: ImageInput[] = [];
    const processedNames: string[] = [];

    for (const fp of filePaths) {
      const fullPath = join(WORKSPACE_ROOT, fp);

      let content: Buffer;
      try {
        content = await readFile(fullPath);
      } catch (e) {
        if (
          e instanceof Error &&
          "code" in e &&
          (e as NodeJS.ErrnoException).code === "ENOENT"
        ) {
          return {
            content: [
              { type: "text", text: `ファイルが見つかりません: ${fp}` },
            ],
            is_error: true,
          };
        }
        throw e;
      }

      const filename = basename(fullPath);
      const contentType = guessMimeType(fullPath);

      // 画像カテゴリチェック
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

      // Vision API 対応フォーマットチェック
      if (contentType && !SUPPORTED_VISION_FORMATS.has(contentType)) {
        return {
          content: [
            {
              type: "text",
              text:
                `Vision API 非対応フォーマットです: ${filename} (${contentType})\n` +
                "対応フォーマット: JPEG, PNG, GIF, WebP",
            },
          ],
          is_error: true,
        };
      }

      // リサイズ
      const [resizedContent, finalContentType] = await resizeImageIfNeeded(
        content,
        contentType,
        maxDimension,
      );

      const format = toImageFormat(finalContentType);
      if (!format) {
        return {
          content: [
            {
              type: "text",
              text: `画像フォーマットの変換に失敗しました: ${filename} (${finalContentType})`,
            },
          ],
          is_error: true,
        };
      }

      images.push({ buffer: resizedContent, format });
      processedNames.push(fp);
    }

    // Bedrock Vision API で分析
    const result = await analyzeImagesWithBedrock({ images, prompt });

    return {
      content: [{ type: "text", text: result.text }],
    };
  } catch (e) {
    const paths = filePaths.join(", ");
    logger.error({ err: e, filePaths: paths }, "画像分析エラー");
    return {
      content: [
        {
          type: "text",
          text: `画像分析エラー: ${e instanceof Error ? e.message : String(e)}`,
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
