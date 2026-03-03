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
  aws_region: z.string().default("ap-northeast-1"),
  bedrock_model_id: z.string().default(""),
});

export type WorkspaceSyncConfig = z.infer<typeof WorkspaceSyncConfigSchema>;
export type InvocationRequest = z.infer<typeof InvocationRequestSchema>;

// =============================================================================
// SSE イベント型
// =============================================================================

/** モデル別使用量（Host API に送信 — UsageInfo 仕様準拠） */
export interface ModelTokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_creation_5m_tokens: number;
  cache_creation_1h_tokens: number;
  cache_read_tokens: number;
  web_search_requests: number;
}

/** ヘルスチェックレスポンス */
export interface HealthResponse {
  status: string;
}
