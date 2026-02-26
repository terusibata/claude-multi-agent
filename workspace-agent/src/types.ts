/**
 * ワークスペースエージェント リクエスト/レスポンス型定義
 * AgentCore Runtime の /invocations エンドポイント向けスキーマ
 */
import { z } from "zod";

// =============================================================================
// リクエストスキーマ
// =============================================================================

/** S3 ワークスペース同期設定 */
export const WorkspaceSyncConfigSchema = z.object({
  enabled: z.boolean().default(false),
  s3_bucket: z.string().default(""),
  s3_prefix: z.string().default("workspaces/"),
  tenant_id: z.string().default(""),
  conversation_id: z.string().default(""),
});

/** AgentCore /invocations リクエスト */
export const InvocationRequestSchema = z.object({
  user_input: z.string(),
  system_prompt: z.string().default(""),
  model: z.string().default("claude-sonnet-4-5-20250929"),
  session_id: z.string().nullable().default(null),
  max_turns: z.number().nullable().default(null),
  allowed_tools: z.array(z.string()).default([]),
  cwd: z.string().default("/workspace"),
  setting_sources: z.array(z.string()).nullable().default(null),
  mcp_server_configs: z.array(z.record(z.string(), z.unknown())).nullable().default(null),

  // AgentCore 固有フィールド
  workspace_sync: WorkspaceSyncConfigSchema.nullable().default(null),
  skill_files: z.record(z.string(), z.string()).nullable().default(null),
  aws_region: z.string().default("us-west-2"),
  bedrock_model_id: z.string().default(""),
});

export type WorkspaceSyncConfig = z.infer<typeof WorkspaceSyncConfigSchema>;
export type InvocationRequest = z.infer<typeof InvocationRequestSchema>;

// =============================================================================
// SSE イベント型
// =============================================================================

/** モデル別使用量（Host API に送信） */
export interface ModelTokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  web_search_requests: number;
}

/** done イベントデータ */
export interface DoneEventData {
  subtype: string;
  result: string | null;
  session_id: string | null;
  num_turns: number;
  duration_ms: number;
  cost_usd: number;
  usage: Record<string, number>;
  model_usage?: Record<string, ModelTokenUsage>;
}

/** ヘルスチェックレスポンス */
export interface HealthResponse {
  status: string;
}
