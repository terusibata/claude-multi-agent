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
import { uploadFilesToS3 } from "./agent-file-sync.js";

/** S3 同期設定（present_files 時の即時アップロードに使用） */
export interface S3Config {
  s3Bucket: string;
  s3Prefix: string;
  tenantId: string;
  conversationId: string;
  region?: string;
}

const logger = createLogger("builtin-mcp");

const WORKSPACE_DIR = "/workspace";

// =============================================================================
// file-presentation MCP サーバー
// =============================================================================

function createFilePresentationServer(s3Config?: S3Config): McpServerConfig | null {
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

      // present_files 時にファイルを即座に S3 にアップロード
      // フロントエンドがツール結果を受信した時点でダウンロード API を呼ぶため、
      // セッション終了を待たずに S3 に配置する必要がある
      if (s3Config && s3Config.s3Bucket && existingFiles.length > 0) {
        const fullPaths = existingFiles.map((f) => f.path as string);
        try {
          const uploaded = await uploadFilesToS3(s3Config, fullPaths);
          logger.info({ msg: "present_files: S3即時アップロード完了", count: uploaded });
        } catch (e) {
          logger.error({ msg: "present_files: S3即時アップロードエラー", error: String(e) });
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
  // NOTE: PDF/Excel/Word/PowerPoint/画像メタデータの読み取りはDefault Skills（Python）に移行済み
  const toolSchemas: Record<string, { description: string; schema: z.ZodRawShape }> = {
    list_workspace_files: {
      description: "ワークスペース内のファイル一覧を取得する",
      schema: { filter_type: z.string().default("all") },
    },
    read_image_file: {
      description:
        "画像ファイルをAIで分析しテキストで返す（視覚的理解）。複数画像の一括分析も可能",
      schema: {
        file_path: z
          .string()
          .optional()
          .describe("画像ファイルのパス（/workspaceからの相対パス）"),
        file_paths: z
          .array(z.string())
          .optional()
          .describe(
            "画像ファイルのパス（複数指定、最大20枚）",
          ),
        prompt: z
          .string()
          .default(
            "この画像の内容を詳細に説明してください。テキストが含まれる場合は読み取ってください。",
          )
          .describe(
            "何を知りたいかの指示（例: 'グラフの数値を読み取って', 'テキストをOCRして'）",
          ),
        max_dimension: z
          .number()
          .default(1568)
          .describe("リサイズ時の最大辺(px)"),
      },
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

export function createBuiltinMcpServers(s3Config?: S3Config): Record<string, McpServerConfig> {
  const servers: Record<string, McpServerConfig> = {};

  try {
    const fileToolsServer = createFileToolsServer();
    if (fileToolsServer) servers["file-tools"] = fileToolsServer;
  } catch (e) {
    logger.error({ msg: "file-tools MCP server creation failed", error: String(e) });
  }

  try {
    const presentationServer = createFilePresentationServer(s3Config);
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
