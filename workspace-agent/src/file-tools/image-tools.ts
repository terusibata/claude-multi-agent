/**
 * Image file tools
 *
 * inspect_image_file: Metadata inspection (resolution, size)
 * Note: read_image_file is implemented in registry.ts
 */

import sharp from "sharp";
import { createLogger } from "../logger.js";
import {
  localFileToolHandler,
  formatToolError,
  formatToolSuccess,
  type ToolResult,
} from "./utils.js";

const logger = createLogger("image-tools");

// Supported image MIME types
const IMAGE_MIME_TYPES = new Set([
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/gif",
  "image/webp",
  "image/bmp",
  "image/tiff",
  "image/svg+xml",
]);

// Supported image extensions
const IMAGE_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".gif",
  ".webp",
  ".bmp",
  ".tiff",
  ".tif",
  ".svg",
]);

/**
 * Check if a file is an image based on content type or extension
 */
export function isImageFile(
  filename: string,
  contentType: string | null,
): boolean {
  if (contentType && IMAGE_MIME_TYPES.has(contentType)) {
    return true;
  }

  // Fallback to extension check
  const lowerName = filename.toLowerCase();
  for (const ext of IMAGE_EXTENSIONS) {
    if (lowerName.endsWith(ext)) {
      return true;
    }
  }

  return false;
}

/**
 * inspect_image_file handler - Get image file metadata
 */
export const inspectImageFileHandler = localFileToolHandler(
  { logPrefix: "画像情報取得" },
  async ({ content, filename, contentType }): Promise<ToolResult> => {
    // Check if it's an image file
    if (!isImageFile(filename, contentType)) {
      return formatToolError(
        `このファイルは画像ではありません: ${filename} (${contentType})`,
      );
    }

    const resultLines: string[] = [
      `# 画像情報: ${filename}`,
      `ファイルサイズ: ${(content.length / 1024).toFixed(1)} KB`,
      `MIMEタイプ: ${contentType}`,
    ];

    // Get detailed info using sharp
    try {
      const metadata = await sharp(content).metadata();

      if (metadata.width && metadata.height) {
        resultLines.push(`解像度: ${metadata.width} x ${metadata.height} px`);
      }
      if (metadata.space) {
        resultLines.push(`カラーモード: ${metadata.space}`);
      }
      if (metadata.format) {
        resultLines.push(`フォーマット: ${metadata.format}`);
      }
      if (metadata.density) {
        resultLines.push(`DPI: ${metadata.density}`);
      }
      if (metadata.pages && metadata.pages > 1) {
        resultLines.push(`フレーム数: ${metadata.pages}`);
      }
      if (metadata.hasAlpha !== undefined) {
        resultLines.push(
          `アルファチャンネル: ${metadata.hasAlpha ? "あり" : "なし"}`,
        );
      }

      // EXIF info (if available)
      if (metadata.exif) {
        try {
          // sharp provides raw EXIF buffer; we report its presence
          resultLines.push("");
          resultLines.push("## EXIF情報");
          resultLines.push("- EXIF データが含まれています");
          if (metadata.orientation) {
            resultLines.push(`- 回転情報: ${metadata.orientation}`);
          }
        } catch {
          // EXIF parsing failed, skip
        }
      }
    } catch (e) {
      logger.warn({ err: e }, "画像詳細情報取得エラー");
      resultLines.push("");
      resultLines.push(
        `※ 詳細情報の取得に失敗: ${e instanceof Error ? e.message : String(e)}`,
      );
    }

    let resultText = resultLines.join("\n");
    resultText += "\n\n---\n";
    resultText +=
      "画像を視覚的に確認するには `read_image_file` を使用してください。";

    return formatToolSuccess(resultText);
  },
);
