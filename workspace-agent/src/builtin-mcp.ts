/**
 * コンテナ内ビルトイン MCP サーバーファクトリ (TypeScript)
 *
 * ビルトイン MCP サーバー（file-tools, file-presentation）と
 * OpenAPI MCP サーバーを作成する。
 */
import { createSdkMcpServer, tool } from "@anthropic-ai/claude-agent-sdk";
import type { McpServerConfig } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import * as fs from "node:fs";
import * as path from "node:path";
import { createLogger } from "./logger.js";
import { createFileToolHandlers } from "./file-tools/registry.js";
import { OpenAPIMcpService } from "./openapi-mcp.js";

const logger = createLogger("builtin-mcp");

const WORKSPACE_DIR = "/workspace";

// =============================================================================
// file-presentation MCP サーバー
// =============================================================================

function createFilePresentationServer(): McpServerConfig | null {
  const presentFilesTool = tool(
    "present_files",
    "AIが作成・編集したファイルをユーザーに提示する。" +
      "ファイルパスのリストと説明を受け取り、ユーザーに提示する情報を返す。" +
      "Write/Edit/NotebookEditでファイルを作成・編集した後は、" +
      "必ずこのツールを使用してユーザーにファイルを提示してください。",
    { file_paths: z.array(z.string()), description: z.string() },
    async (args) => {
      let filePaths: string[] = args.file_paths;

      // file_paths の正規化
      if (typeof filePaths === "string") {
        try {
          const parsed = JSON.parse(filePaths as unknown as string);
          if (Array.isArray(parsed)) filePaths = parsed;
        } catch {
          filePaths = [filePaths as unknown as string];
        }
      }

      const existingFiles: Record<string, unknown>[] = [];
      const missingFiles: Record<string, unknown>[] = [];

      for (const filePath of filePaths) {
        const fullPath = path.isAbsolute(filePath)
          ? filePath
          : path.join(WORKSPACE_DIR, filePath);

        try {
          const stat = fs.statSync(fullPath);
          if (stat.isFile()) {
            let relativePath: string;
            try {
              relativePath = path.relative(WORKSPACE_DIR, fullPath);
            } catch {
              relativePath = path.basename(fullPath);
            }

            existingFiles.push({
              path: fullPath,
              relative_path: relativePath,
              name: path.basename(fullPath),
              size: stat.size,
              mime_type: getMimeType(fullPath),
              exists: true,
            });
          } else {
            missingFiles.push({ relative_path: filePath, exists: false });
          }
        } catch {
          missingFiles.push({ relative_path: filePath, exists: false });
        }
      }

      const parts: string[] = [];
      if (existingFiles.length > 0) {
        parts.push(`ファイルを提示しました: ${args.description}\n`);
        parts.push("【提示されたファイル】");
        for (const f of existingFiles) {
          parts.push(`• ${f.name} (${f.size} bytes)`);
          parts.push(`  ダウンロードパス: ${f.relative_path}`);
        }
      } else {
        parts.push(`提示するファイルが見つかりませんでした: ${args.description}`);
      }

      if (missingFiles.length > 0) {
        parts.push("");
        parts.push("【見つからなかったファイル】");
        for (const f of missingFiles) {
          parts.push(`• ${f.relative_path}`);
        }
      }

      return {
        content: [{ type: "text" as const, text: parts.join("\n") }],
      };
    },
  );

  return createSdkMcpServer({
    name: "file-presentation",
    version: "1.0.0",
    tools: [presentFilesTool],
  });
}

// =============================================================================
// file-tools MCP サーバー
// =============================================================================

function createFileToolsServer(): McpServerConfig | null {
  const handlers = createFileToolHandlers();

  // ツール定義（名前 → 説明 + Zod スキーマ）
  const toolSchemas: Record<string, { description: string; schema: z.ZodRawShape }> = {
    list_workspace_files: {
      description: "ワークスペース内のファイル一覧を取得する",
      schema: { filter_type: z.string().default("all") },
    },
    read_image_file: {
      description: "画像ファイルを視覚的に読み込む（base64エンコード）",
      schema: { file_path: z.string(), max_dimension: z.number().default(1920) },
    },
    get_sheet_info: {
      description: "Excelファイルのシート一覧と基本情報を取得する",
      schema: { file_path: z.string() },
    },
    get_sheet_csv: {
      description: "Excelシートの内容をCSV形式で取得する（行範囲指定可能）",
      schema: {
        file_path: z.string(),
        sheet_name: z.string().default(""),
        start_row: z.number().nullable().default(null),
        end_row: z.number().nullable().default(null),
      },
    },
    search_workbook: {
      description: "Excelワークブック全体からキーワード検索する",
      schema: { file_path: z.string(), query: z.string(), case_sensitive: z.boolean().default(false) },
    },
    inspect_pdf_file: {
      description: "PDFファイルの基本情報（ページ数、メタデータ等）を取得する",
      schema: { file_path: z.string() },
    },
    read_pdf_pages: {
      description: "PDFの指定ページのテキストを読み取る（pages例: '1-5', '1,3,5'）",
      schema: { file_path: z.string(), pages: z.string().default("1-10") },
    },
    convert_pdf_to_images: {
      description: "PDFページを画像に変換する（図表確認用）（pages例: '1-3'）",
      schema: { file_path: z.string(), pages: z.string().default("1"), dpi: z.number().default(150) },
    },
    get_document_info: {
      description: "Word文書の基本情報（段落数、セクション等）を取得する",
      schema: { file_path: z.string() },
    },
    get_document_content: {
      description: "Word文書のテキスト内容を取得する",
      schema: {
        file_path: z.string(),
        start_paragraph: z.number().nullable().default(null),
        end_paragraph: z.number().nullable().default(null),
      },
    },
    search_document: {
      description: "Word文書内をキーワード検索する",
      schema: { file_path: z.string(), query: z.string(), case_sensitive: z.boolean().default(false) },
    },
    get_presentation_info: {
      description: "PowerPointプレゼンテーションの基本情報を取得する",
      schema: { file_path: z.string() },
    },
    get_slides_content: {
      description: "PowerPointスライドのテキスト内容を取得する（slides例: '1-5', '1,3,5'）",
      schema: {
        file_path: z.string(),
        slides: z.string().default("1-10"),
        max_slides: z.number().default(10),
      },
    },
    search_presentation: {
      description: "PowerPointプレゼンテーション内をキーワード検索する",
      schema: { file_path: z.string(), query: z.string(), case_sensitive: z.boolean().default(false) },
    },
    inspect_image_file: {
      description: "画像ファイルの基本情報（サイズ、フォーマット等）を取得する",
      schema: { file_path: z.string() },
    },
  };

  const tools = [];

  for (const [handlerName, handlerFunc] of Object.entries(handlers)) {
    const schemaInfo = toolSchemas[handlerName];
    if (!schemaInfo) {
      logger.warn({ msg: "Unknown handler, skipping", handler: handlerName });
      continue;
    }

    const fn = handlerFunc;
    const t = tool(
      handlerName,
      schemaInfo.description,
      schemaInfo.schema,
      async (args) => fn(args as Record<string, unknown>),
    );
    tools.push(t);
  }

  if (tools.length === 0) {
    logger.warn({ msg: "No file tools created" });
    return null;
  }

  return createSdkMcpServer({
    name: "file-tools",
    version: "1.0.0",
    tools,
  });
}

// =============================================================================
// パブリック API
// =============================================================================

export function createBuiltinMcpServers(): Record<string, McpServerConfig> {
  const servers: Record<string, McpServerConfig> = {};

  try {
    const fileToolsServer = createFileToolsServer();
    if (fileToolsServer) servers["file-tools"] = fileToolsServer;
  } catch (e) {
    logger.error({ msg: "file-tools MCP server creation failed", error: String(e) });
  }

  try {
    const presentationServer = createFilePresentationServer();
    if (presentationServer) servers["file-presentation"] = presentationServer;
  } catch (e) {
    logger.error({ msg: "file-presentation MCP server creation failed", error: String(e) });
  }

  return servers;
}

export function createOpenApiMcpServers(
  configs: Record<string, unknown>[],
): Record<string, McpServerConfig> {
  const servers: Record<string, McpServerConfig> = {};

  for (const config of configs) {
    try {
      const serverName = config.server_name as string;
      const openapiSpec = config.openapi_spec as Record<string, unknown>;
      const baseUrl = config.base_url as string | undefined;
      const headers = config.headers as Record<string, string> | undefined;

      const service = new OpenAPIMcpService({
        openapiSpec,
        serverName,
        baseUrl,
        headers,
      });

      const toolDefinitions = service.getToolDefinitions();
      if (toolDefinitions.length === 0) {
        logger.warn({ msg: "No tools found in OpenAPI spec", server: serverName });
        continue;
      }

      const tools = toolDefinitions.map((td) => {
        const handler = service.createToolHandler(td.name);
        const schemaDict: Record<string, z.ZodType> = {};
        for (const [propName, propDef] of Object.entries(
          td.input_schema.properties ?? {},
        )) {
          const propType = (propDef as Record<string, string>).type ?? "string";
          switch (propType) {
            case "integer":
            case "number":
              schemaDict[propName] = z.number().optional();
              break;
            case "boolean":
              schemaDict[propName] = z.boolean().optional();
              break;
            default:
              schemaDict[propName] = z.string().optional();
          }
        }

        return tool(td.name, td.description, schemaDict as z.ZodRawShape, async (args) =>
          handler(args as Record<string, unknown>),
        );
      });

      const server = createSdkMcpServer({
        name: serverName,
        version: "1.0.0",
        tools,
      });

      servers[serverName] = server;
    } catch (e) {
      logger.error({
        msg: "OpenAPI MCP server creation failed",
        error: String(e),
        server: (config as Record<string, unknown>).server_name,
      });
    }
  }

  return servers;
}

// =============================================================================
// ユーティリティ
// =============================================================================

const MIME_MAP: Record<string, string> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".pdf": "application/pdf",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".json": "application/json",
  ".xml": "application/xml",
  ".txt": "text/plain",
  ".csv": "text/csv",
  ".html": "text/html",
  ".md": "text/markdown",
};

function getMimeType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  return MIME_MAP[ext] ?? "application/octet-stream";
}
