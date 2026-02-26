/**
 * OpenAPI 仕様から MCP サーバーを動的に生成するサービス (TypeScript)
 *
 * OpenAPI 仕様を解析し、各エンドポイントを MCP ツールに変換。
 * ツール実行時に HTTP API を呼び出すプロキシとして機能。
 */
import { createLogger } from "./logger.js";

const logger = createLogger("openapi-mcp");

// =============================================================================
// 型定義
// =============================================================================

interface ToolDefinition {
  name: string;
  description: string;
  input_schema: {
    type: string;
    properties: Record<string, Record<string, unknown>>;
    required: string[];
  };
}

interface OperationInfo {
  method: string;
  path: string;
  operation: Record<string, unknown>;
}

interface ToolResult {
  content: Array<{ type: string; text: string }>;
  is_error?: boolean;
  _metadata?: Record<string, unknown>;
}

interface OpenAPIMcpServiceOptions {
  openapiSpec: Record<string, unknown>;
  baseUrl?: string;
  headers?: Record<string, string>;
  serverName?: string;
  verifySsl?: boolean;
  timeout?: number;
}

// =============================================================================
// OpenAPIMcpService
// =============================================================================

export class OpenAPIMcpService {
  static readonly DEFAULT_TIMEOUT = 30_000;
  static readonly MAX_RESPONSE_SIZE = 10 * 1024 * 1024;
  static readonly MAX_REF_DEPTH = 10;

  private openapiSpec: Record<string, unknown>;
  private baseUrl: string;
  private headers: Record<string, string>;
  private serverName: string;
  private timeout: number;
  private toolDefinitions: ToolDefinition[] = [];
  private operationMap: Map<string, OperationInfo> = new Map();

  constructor(options: OpenAPIMcpServiceOptions) {
    this.openapiSpec = options.openapiSpec;
    this.headers = options.headers ?? {};
    this.serverName = options.serverName ?? "openapi";
    this.timeout = options.timeout ?? OpenAPIMcpService.DEFAULT_TIMEOUT;

    if (options.baseUrl) {
      this.baseUrl = options.baseUrl.replace(/\/$/, "");
    } else {
      const servers = (this.openapiSpec.servers ?? []) as Array<Record<string, string>>;
      this.baseUrl = servers[0]?.url?.replace(/\/$/, "") ?? "";
    }

    if (this.baseUrl && !this.baseUrl.startsWith("http://") && !this.baseUrl.startsWith("https://")) {
      logger.warn({ msg: "Invalid base_url", baseUrl: this.baseUrl, server: this.serverName });
      this.baseUrl = "";
    }
  }

  parseSpec(): ToolDefinition[] {
    this.toolDefinitions = [];
    this.operationMap.clear();

    const paths = (this.openapiSpec.paths ?? {}) as Record<string, Record<string, unknown>>;

    for (const [pathStr, pathItem] of Object.entries(paths)) {
      if (typeof pathItem !== "object" || pathItem === null) continue;

      for (const method of ["get", "post", "put", "patch", "delete"]) {
        const operation = pathItem[method] as Record<string, unknown> | undefined;
        if (!operation || typeof operation !== "object") continue;

        let operationId = operation.operationId as string | undefined;
        if (!operationId) {
          operationId = this.generateOperationId(method, pathStr);
        }

        const toolDef = this.createToolDefinition(operationId, method.toUpperCase(), pathStr, operation);
        this.toolDefinitions.push(toolDef);
        this.operationMap.set(operationId, {
          method: method.toUpperCase(),
          path: pathStr,
          operation,
        });
      }
    }

    return this.toolDefinitions;
  }

  getToolDefinitions(): ToolDefinition[] {
    if (this.toolDefinitions.length === 0) {
      this.parseSpec();
    }
    return this.toolDefinitions;
  }

  getAllowedTools(): string[] {
    return this.getToolDefinitions().map(
      (t) => `mcp__${this.serverName}__${t.name}`,
    );
  }

  createToolHandler(toolName: string): (args: Record<string, unknown>) => Promise<ToolResult> {
    return async (args: Record<string, unknown>) => this.executeTool(toolName, args);
  }

  // ===========================================================================
  // 内部メソッド
  // ===========================================================================

  private generateOperationId(method: string, pathStr: string): string {
    const cleanPath = pathStr.replace(/[{}]/g, "").replace(/[^a-zA-Z0-9/]/g, "");
    const parts = cleanPath.split("/").filter(Boolean);
    return `${method}_${parts.join("_")}`;
  }

  private resolveRef(ref: string, depth = 0): Record<string, unknown> {
    if (depth > OpenAPIMcpService.MAX_REF_DEPTH) return { type: "object" };
    if (!ref.startsWith("#/")) return { type: "object" };

    const parts = ref.slice(2).split("/");
    let current: unknown = this.openapiSpec;

    try {
      for (const part of parts) {
        const decodedPart = part.replace(/~1/g, "/").replace(/~0/g, "~");
        current = (current as Record<string, unknown>)[decodedPart];
      }
    } catch {
      return { type: "object" };
    }

    if (typeof current === "object" && current !== null && "$ref" in current) {
      return this.resolveRef((current as Record<string, string>).$ref, depth + 1);
    }

    return typeof current === "object" && current !== null
      ? (current as Record<string, unknown>)
      : { type: "object" };
  }

  private resolveSchema(schema: Record<string, unknown>, depth = 0): Record<string, unknown> {
    if (depth > OpenAPIMcpService.MAX_REF_DEPTH) return schema;
    if (typeof schema !== "object" || schema === null) return schema;

    if ("$ref" in schema) {
      const resolved = this.resolveRef(schema.$ref as string, depth);
      const otherProps = Object.fromEntries(
        Object.entries(schema).filter(([k]) => k !== "$ref"),
      );
      if (Object.keys(otherProps).length > 0) {
        return this.mergeSchemas([resolved, otherProps], depth + 1);
      }
      return this.resolveSchema(resolved, depth + 1);
    }

    if ("allOf" in schema) {
      const allSchemas = (schema.allOf as Record<string, unknown>[]).map((s) =>
        this.resolveSchema(s, depth + 1),
      );
      let merged = this.mergeSchemas(allSchemas, depth + 1);
      const otherProps = Object.fromEntries(
        Object.entries(schema).filter(([k]) => k !== "allOf"),
      );
      if (Object.keys(otherProps).length > 0) {
        merged = this.mergeSchemas([merged, otherProps], depth + 1);
      }
      return merged;
    }

    for (const keyword of ["oneOf", "anyOf"]) {
      if (keyword in schema) {
        const variants = schema[keyword] as Record<string, unknown>[];
        if (variants?.length) {
          const firstSchema = this.resolveSchema(variants[0], depth + 1);
          const otherProps = Object.fromEntries(
            Object.entries(schema).filter(([k]) => k !== keyword),
          );
          if (Object.keys(otherProps).length > 0) {
            return this.mergeSchemas([firstSchema, otherProps], depth + 1);
          }
          return firstSchema;
        }
        return { type: "object" };
      }
    }

    if ("properties" in schema) {
      const resolvedProps: Record<string, unknown> = {};
      for (const [propName, propSchema] of Object.entries(
        schema.properties as Record<string, Record<string, unknown>>,
      )) {
        resolvedProps[propName] = this.resolveSchema(propSchema, depth + 1);
      }
      return { ...schema, properties: resolvedProps };
    }

    if ("items" in schema) {
      return {
        ...schema,
        items: this.resolveSchema(schema.items as Record<string, unknown>, depth + 1),
      };
    }

    return schema;
  }

  private mergeSchemas(
    schemas: Record<string, unknown>[],
    _depth = 0,
  ): Record<string, unknown> {
    const merged: Record<string, unknown> = {
      type: "object",
      properties: {} as Record<string, unknown>,
      required: [] as string[],
    };

    for (const schema of schemas) {
      if (typeof schema !== "object" || schema === null) continue;
      if ("type" in schema) merged.type = schema.type;
      if ("properties" in schema) {
        Object.assign(
          merged.properties as Record<string, unknown>,
          schema.properties as Record<string, unknown>,
        );
      }
      if ("required" in schema) {
        for (const req of schema.required as string[]) {
          if (!(merged.required as string[]).includes(req)) {
            (merged.required as string[]).push(req);
          }
        }
      }
      if ("description" in schema) merged.description = schema.description;
    }

    if ((merged.required as string[]).length === 0) {
      delete merged.required;
    }

    return merged;
  }

  private createToolDefinition(
    operationId: string,
    method: string,
    pathStr: string,
    operation: Record<string, unknown>,
  ): ToolDefinition {
    const summary = (operation.summary as string) ?? "";
    const description = (operation.description as string) ?? "";
    let fullDescription = description
      ? `${summary}\n\n${description}`.trim()
      : summary;
    if (!fullDescription) fullDescription = `${method} ${pathStr}`;

    const properties: Record<string, Record<string, unknown>> = {};
    const required: string[] = [];

    const pathParams = [...pathStr.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);

    for (const param of (operation.parameters ?? []) as Record<string, unknown>[]) {
      const paramName = param.name as string;
      const paramIn = param.in as string;
      if (paramIn === "header") continue;

      const paramSchema = (param.schema ?? { type: "string" }) as Record<string, unknown>;
      const resolvedParamSchema =
        typeof paramSchema === "object" ? this.resolveSchema(paramSchema) : { type: "string" };

      properties[paramName] = {
        type: (resolvedParamSchema.type as string) ?? "string",
        description: `${(param.description as string) ?? ""} (${paramIn} parameter)`,
      };

      if (resolvedParamSchema.default !== undefined) {
        properties[paramName].default = resolvedParamSchema.default;
      }

      if (param.required || pathParams.includes(paramName)) {
        required.push(paramName);
      }
    }

    const requestBody = (operation.requestBody ?? {}) as Record<string, unknown>;
    if (Object.keys(requestBody).length > 0) {
      const content = (requestBody.content ?? {}) as Record<string, Record<string, unknown>>;
      const jsonContent = content["application/json"] ?? {};
      const bodySchema = (jsonContent.schema ?? {}) as Record<string, unknown>;

      if (Object.keys(bodySchema).length > 0) {
        const resolved = this.resolveSchema(bodySchema);
        const bodyProps = (resolved.properties ?? {}) as Record<string, Record<string, unknown>>;

        for (const [propName, propDef] of Object.entries(bodyProps)) {
          const resolvedProp =
            typeof propDef === "object" ? this.resolveSchema(propDef) : propDef;
          properties[propName] = {
            type:
              typeof resolvedProp === "object"
                ? ((resolvedProp as Record<string, string>).type ?? "string")
                : "string",
            description:
              typeof resolvedProp === "object"
                ? ((resolvedProp as Record<string, string>).description ?? `Request body field: ${propName}`)
                : `Request body field: ${propName}`,
          };
        }

        const bodyRequired = (resolved.required ?? []) as string[];
        required.push(...bodyRequired);
      }
    }

    return {
      name: operationId,
      description: fullDescription,
      input_schema: {
        type: "object",
        properties,
        required: [...new Set(required)],
      },
    };
  }

  private encodePathParameter(value: unknown): string {
    if (value == null) return "";
    return encodeURIComponent(String(value));
  }

  async executeTool(
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<ToolResult> {
    const opInfo = this.operationMap.get(toolName);
    if (!opInfo) {
      return { content: [{ type: "text", text: `Unknown tool: ${toolName}` }], is_error: true };
    }

    if (!this.baseUrl) {
      return { content: [{ type: "text", text: "API base URL is not configured" }], is_error: true };
    }

    const { method, path: pathTemplate, operation } = opInfo;

    // パスパラメータを置換
    let resolvedPath = pathTemplate;
    const pathParams = [...pathTemplate.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
    for (const paramName of pathParams) {
      if (paramName in args) {
        resolvedPath = resolvedPath.replace(
          `{${paramName}}`,
          this.encodePathParameter(args[paramName]),
        );
      } else {
        return {
          content: [{ type: "text", text: `Missing required path parameter: ${paramName}` }],
          is_error: true,
        };
      }
    }

    // クエリパラメータ
    const queryParams: Record<string, string> = {};
    for (const param of (operation.parameters ?? []) as Record<string, unknown>[]) {
      const paramName = param.name as string;
      const paramIn = param.in as string;
      if (paramName in args && paramIn === "query") {
        queryParams[paramName] = String(args[paramName]);
      }
    }

    // リクエストボディ
    const bodyData: Record<string, unknown> = {};
    const requestBody = (operation.requestBody ?? {}) as Record<string, unknown>;
    if (Object.keys(requestBody).length > 0) {
      const content = (requestBody.content ?? {}) as Record<string, Record<string, unknown>>;
      if ("application/json" in content) {
        const bodySchema = (content["application/json"].schema ?? {}) as Record<string, unknown>;
        const bodyProps = (bodySchema.properties ?? {}) as Record<string, unknown>;
        for (const propName of Object.keys(bodyProps)) {
          if (propName in args) {
            bodyData[propName] = args[propName];
          }
        }
      }
    }

    // URL 構築
    const queryString = Object.keys(queryParams).length > 0
      ? `?${new URLSearchParams(queryParams).toString()}`
      : "";
    const url = `${this.baseUrl}${resolvedPath}${queryString}`;

    try {
      const fetchOptions: RequestInit = {
        method,
        headers: { ...this.headers },
        signal: AbortSignal.timeout(this.timeout),
      };

      if (Object.keys(bodyData).length > 0 && !["GET", "DELETE"].includes(method)) {
        fetchOptions.body = JSON.stringify(bodyData);
        (fetchOptions.headers as Record<string, string>)["Content-Type"] = "application/json";
      }

      const response = await fetch(url, fetchOptions);

      if (!response.ok) {
        const errorText = await response.text();
        return {
          content: [{ type: "text", text: `HTTP ${response.status}: ${errorText.slice(0, 500)}` }],
          is_error: true,
        };
      }

      const contentType = response.headers.get("content-type") ?? "";
      let resultText: string;
      if (contentType.includes("application/json")) {
        try {
          const data = await response.json();
          resultText = JSON.stringify(data, null, 2);
        } catch {
          resultText = await response.text();
        }
      } else {
        resultText = await response.text();
      }

      return {
        content: [{ type: "text", text: resultText }],
        _metadata: { status_code: response.status, url, method },
      };
    } catch (e) {
      if (e instanceof DOMException && e.name === "TimeoutError") {
        return {
          content: [{ type: "text", text: `Request timeout after ${this.timeout}ms` }],
          is_error: true,
        };
      }
      logger.error({ msg: "OpenAPI tool execution error", error: String(e), tool: toolName, url });
      return { content: [{ type: "text", text: `Error: ${String(e)}` }], is_error: true };
    }
  }
}
