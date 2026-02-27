/**
 * File tools common utilities
 *
 * MIME type mapping, workspace root, and shared types for file tools.
 */

import { extname } from "node:path";

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
// Workspace Root
// =============================================================================

export const WORKSPACE_ROOT = "/workspace";
