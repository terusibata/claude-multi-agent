/**
 * Claude Agent SDK クライアントラッパー (TypeScript)
 *
 * コンテナ内で TS SDK を起動し、SSE ストリームを生成する。
 * Python SDK では取得できなかった modelUsage（モデル別使用量）を正確に取得する。
 *
 * AgentCore Runtime版: 実行ロールで直接 Bedrock にアクセス
 *
 * SDK API (@anthropic-ai/claude-agent-sdk):
 *   - query({ prompt, options }) -> AsyncGenerator<SDKMessage>
 *   - SDKMessage = SDKUserMessage | SDKAssistantMessage | SDKSystemMessage | SDKResultMessage
 */
import { query } from "@anthropic-ai/claude-agent-sdk";
import type {
  SDKMessage,
  SDKResultMessage,
  SDKAssistantMessage,
  SDKUserMessage,
  SDKSystemMessage,
  Options,
  McpServerConfig,
} from "@anthropic-ai/claude-agent-sdk";
import { createLogger } from "./logger.js";
import type { InvocationRequest, ModelTokenUsage } from "./types.js";
import { createBuiltinMcpServers, createOpenApiMcpServers } from "./builtin-mcp.js";

const logger = createLogger("sdk-client");

// =============================================================================
// SSE フォーマッタ
// =============================================================================

function formatSSE(eventType: string, data: Record<string, unknown>): string {
  return `event: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
}

// =============================================================================
// SDK オプション構築
// =============================================================================

function buildSdkOptions(request: InvocationRequest): Options {
  const env: Record<string, string> = {
    CLAUDE_CODE_USE_BEDROCK: process.env.CLAUDE_CODE_USE_BEDROCK ?? "1",
    AWS_REGION: request.aws_region || process.env.AWS_REGION || "us-west-2",
    // NODE_OPTIONS を明示的にクリア（CLI バイナリが壊れるのを防止）
    NODE_OPTIONS: "",
    HOME: process.env.HOME ?? "/home/appuser",
    TMPDIR: "/tmp",
    CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR ?? "/home/appuser/.claude",
    CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK: "1",
  };

  const options: Options = {
    model: request.model || undefined,
    cwd: request.cwd,
    systemPrompt: request.system_prompt || undefined,
    maxTurns: request.max_turns ?? undefined,
    permissionMode: "bypassPermissions" as const,
    allowDangerouslySkipPermissions: true,
    env,
    stderr: (line: string) => {
      logger.warn({ msg: "CLI stderr", line: line.trimEnd() });
    },
  };

  // セッション再開
  if (request.session_id) {
    options.resume = request.session_id;
  }

  // ビルトイン MCP サーバー作成（file-tools, file-presentation）
  try {
    const mcpServers: Record<string, McpServerConfig> = {};

    const builtinServers = createBuiltinMcpServers();
    Object.assign(mcpServers, builtinServers);

    // OpenAPI MCP サーバー作成（ホストから受け取った設定を使用）
    if (request.mcp_server_configs) {
      const openapiServers = createOpenApiMcpServers(
        request.mcp_server_configs as Record<string, unknown>[],
      );
      Object.assign(mcpServers, openapiServers);
    }

    if (Object.keys(mcpServers).length > 0) {
      options.mcpServers = mcpServers;
      logger.info({ msg: "MCP servers created", servers: Object.keys(mcpServers) });
    }
  } catch (e) {
    logger.error({ msg: "MCP server creation failed", error: String(e) });
  }

  if (request.allowed_tools?.length) {
    options.allowedTools = request.allowed_tools;
  }

  if (request.setting_sources) {
    options.settingSources = request.setting_sources as Options["settingSources"];
  }

  return options;
}

// =============================================================================
// ストリーミングプロンプト生成は不要 — SDK query() は string でも MCP サーバーを使用可能
// =============================================================================

// =============================================================================
// メッセージ → SSE 変換
// =============================================================================

/**
 * SDK メッセージを SSE イベント文字列のリストに変換
 *
 * ★ ResultMessage では modelUsage を抽出して done イベントに含める
 */
function messageToSSEEvents(
  message: SDKMessage,
  toolNameMap: Map<string, string>,
): string[] {
  const events: string[] = [];

  if (message.type === "assistant") {
    const assistantMsg = message as SDKAssistantMessage;
    // SDKAssistantMessage.message is a BetaMessage which has .content
    const content = assistantMsg.message?.content ?? [];

    // tool_use_id → tool_name マッピングを蓄積
    for (const block of content) {
      if (block.type === "tool_use") {
        toolNameMap.set(block.id, block.name);
      }
    }

    for (const block of content) {
      if (block.type === "text") {
        events.push(formatSSE("text_delta", { text: block.text }));
      } else if (block.type === "tool_use") {
        events.push(
          formatSSE("tool_use", {
            tool_use_id: block.id,
            tool_name: block.name,
            input: block.input,
          }),
        );
      } else if (block.type === "thinking") {
        events.push(formatSSE("thinking", { content: (block as { thinking: string }).thinking }));
      }
    }
  } else if (message.type === "result") {
    const resultMsg = message as SDKResultMessage;

    // ★ modelUsage からモデル別トークン数を抽出（移行の核心）
    // UsageInfo 仕様準拠のフィールド名を使用:
    //   cache_creation_5m_tokens / cache_creation_1h_tokens / cache_read_tokens
    // Claude Code はデフォルトで 5分キャッシュ（type: "ephemeral"）を使用。
    // 1時間キャッシュは ENABLE_PROMPT_CACHING_1H_BEDROCK 環境変数が設定された場合のみ。
    // SDK の cacheCreationInputTokens は 5m/1h 区分なし → 全量を 5m として扱う
    const modelUsageData: Record<string, ModelTokenUsage> = {};
    if (resultMsg.modelUsage) {
      for (const [modelName, usage] of Object.entries(resultMsg.modelUsage)) {
        modelUsageData[modelName] = {
          input_tokens: usage.inputTokens,
          output_tokens: usage.outputTokens,
          cache_creation_5m_tokens: usage.cacheCreationInputTokens,
          cache_creation_1h_tokens: 0,
          cache_read_tokens: usage.cacheReadInputTokens,
          web_search_requests: usage.webSearchRequests,
        };
      }
    }

    // ★ usage を UsageInfo 仕様準拠の snake_case に変換
    // Python 側 normalize_usage が cache_creation_5m_tokens キー存在で冪等判定する
    const rawUsage = resultMsg.usage as Record<string, unknown> | undefined;
    const usageData: Record<string, number> = {};
    if (rawUsage) {
      usageData.input_tokens = (rawUsage.inputTokens as number) ?? (rawUsage.input_tokens as number) ?? 0;
      usageData.output_tokens = (rawUsage.outputTokens as number) ?? (rawUsage.output_tokens as number) ?? 0;
      // SDK の Usage 型は snake_case（cache_creation_input_tokens）、
      // ModelUsage 型は camelCase（cacheCreationInputTokens）を使用。
      // 両方のフォーマットに対応するため全パターンをフォールバック候補に含める
      usageData.cache_creation_5m_tokens =
        (rawUsage.cacheCreationInputTokens as number)
        ?? (rawUsage.cache_creation_input_tokens as number)
        ?? (rawUsage.cache_creation_5m_tokens as number)
        ?? 0;
      usageData.cache_creation_1h_tokens = 0;
      usageData.cache_read_tokens =
        (rawUsage.cacheReadInputTokens as number)
        ?? (rawUsage.cache_read_input_tokens as number)
        ?? (rawUsage.cache_read_tokens as number)
        ?? 0;
    }

    events.push(
      formatSSE("done", {
        subtype: resultMsg.is_error ? "error_during_execution" : "success",
        result: resultMsg.subtype === "success" ? (resultMsg as { result?: string }).result ?? null : null,
        session_id: resultMsg.session_id,
        num_turns: resultMsg.num_turns,
        duration_ms: resultMsg.duration_ms,
        cost_usd: resultMsg.total_cost_usd,
        usage: usageData,
        // ★ model_usage を追加 — Host API でモデル別 AWS コスト計算に使用
        ...(Object.keys(modelUsageData).length > 0 && { model_usage: modelUsageData }),
      }),
    );
  } else if (message.type === "system") {
    const systemMsg = message as SDKSystemMessage;
    events.push(
      formatSSE("system", {
        subtype: systemMsg.subtype,
        data: systemMsg,
      }),
    );
  } else if (message.type === "user") {
    // SDKUserMessage.message is a MessageParam which has .content
    const userMsg = message as SDKUserMessage;
    const msgContent = userMsg.message?.content;
    if (Array.isArray(msgContent)) {
      for (const block of msgContent) {
        if (typeof block === "object" && block !== null && "type" in block && block.type === "tool_result") {
          const toolResult = block as { tool_use_id: string; content?: unknown; is_error?: boolean };
          // content は string | ContentBlock[] の可能性がある
          let contentStr = "";
          if (toolResult.content) {
            if (typeof toolResult.content === "string") {
              contentStr = toolResult.content;
            } else if (Array.isArray(toolResult.content)) {
              contentStr = toolResult.content
                .map((b: unknown) => {
                  if (typeof b === "object" && b !== null && "text" in b) {
                    return (b as { text: string }).text;
                  }
                  return JSON.stringify(b);
                })
                .join("\n");
            } else {
              contentStr = JSON.stringify(toolResult.content);
            }
          }
          events.push(
            formatSSE("tool_result", {
              tool_use_id: toolResult.tool_use_id,
              tool_name: toolNameMap.get(toolResult.tool_use_id) ?? "",
              content: contentStr,
              is_error: toolResult.is_error ?? false,
            }),
          );
        }
      }
    }
  }

  return events;
}

// =============================================================================
// メイン実行関数
// =============================================================================

/**
 * Claude Agent SDK を実行し、SSE イベント文字列を生成する
 *
 * 各イベントは 'event: ...\ndata: {...}\n\n' 形式の文字列
 */
export async function* executeStreaming(
  request: InvocationRequest,
): AsyncGenerator<string, void, undefined> {
  const options = buildSdkOptions(request);
  const hasMcpServers = options.mcpServers && Object.keys(options.mcpServers).length > 0;

  logger.info({
    msg: "SDK実行開始",
    model: request.model,
    cwd: request.cwd,
    mcpServers: hasMcpServers ? Object.keys(options.mcpServers!) : "none",
  });

  try {
    let doneEmitted = false;
    const toolNameMap = new Map<string, string>();

    for await (const message of query({ prompt: request.user_input, options })) {
      const sseEvents = messageToSSEEvents(message, toolNameMap);
      for (const event of sseEvents) {
        if (event.includes("event: done\n")) {
          doneEmitted = true;
        }
        yield event;
      }
    }

    // ResultMessage が来なかった場合のフォールバック
    if (!doneEmitted) {
      yield formatSSE("done", {
        subtype: "success",
        result: null,
        session_id: null,
        num_turns: 0,
        duration_ms: 0,
        cost_usd: 0,
        usage: {},
      });
    }
  } catch (e) {
    const errorMessage = e instanceof Error ? `${e.constructor.name}: ${e.message}` : String(e);
    logger.error({ msg: "SDK実行エラー", error: errorMessage });

    yield formatSSE("error", { message: errorMessage });
    yield formatSSE("done", {
      subtype: "error_during_execution",
      result: null,
      session_id: null,
      num_turns: 0,
      duration_ms: 0,
      cost_usd: 0,
      usage: {},
    });
  }
}
