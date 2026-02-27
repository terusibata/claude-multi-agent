/**
 * ワークスペースエージェント メインアプリケーション (TypeScript)
 *
 * AgentCore Runtime コンテナとして動作する
 *
 * AgentCore サービスコントラクト:
 *   - POST /invocations — 完全な呼出パイプライン
 *   - GET /ping — ヘルスチェック（200 返却）
 *   - GET /health — 後方互換ヘルスチェック
 *
 * ポート 8080 で HTTP リスン
 */
import Fastify from "fastify";
import { createLogger } from "./logger.js";
import { InvocationRequestSchema } from "./types.js";
import type { InvocationRequest, HealthResponse } from "./types.js";
import { executeStreaming } from "./sdk-client.js";
import {
  restoreFromS3,
  syncToS3,
  restoreSessionFile,
  saveSessionFile,
  getFileManifest,
} from "./agent-file-sync.js";
import * as fs from "node:fs";
import * as path from "node:path";

const logger = createLogger("main");
const AGENT_HTTP_PORT = parseInt(process.env.AGENT_HTTP_PORT ?? "8080", 10);

const app = Fastify({ logger: false });

// =============================================================================
// POST /invocations — 完全な呼出パイプライン
// =============================================================================

app.post("/invocations", async (req, reply) => {
  const parsed = InvocationRequestSchema.safeParse(req.body);
  if (!parsed.success) {
    reply.status(400).send({ error: "Invalid request", details: parsed.error.format() });
    return;
  }

  const request = parsed.data;
  logger.info({ msg: "invocation リクエスト受信", model: request.model, cwd: request.cwd });

  // SSE ストリームとして応答
  reply.raw.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });

  const ws = request.workspace_sync;
  let sessionIdFromDone: string | null = null;

  // 1. S3 からワークスペース復元
  if (ws?.enabled && ws.s3_bucket) {
    try {
      await restoreFromS3({
        s3Bucket: ws.s3_bucket,
        s3Prefix: ws.s3_prefix,
        tenantId: ws.tenant_id,
        conversationId: ws.conversation_id,
        region: request.aws_region,
      });
    } catch (e) {
      logger.error({ msg: "ワークスペース復元エラー（続行）", error: String(e) });
    }

    // SDK セッションファイル復元
    if (request.session_id) {
      try {
        await restoreSessionFile({
          s3Bucket: ws.s3_bucket,
          s3Prefix: ws.s3_prefix,
          tenantId: ws.tenant_id,
          conversationId: ws.conversation_id,
          sessionId: request.session_id,
          region: request.aws_region,
        });
      } catch (e) {
        logger.warn({ msg: "セッションファイル復元エラー（続行）", error: String(e) });
      }
    }
  }

  // 2. スキルファイル書き出し
  if (request.skill_files) {
    writeSkillFiles(request.skill_files);
  }

  // 3. SDK 実行（SSE ストリーム）
  for await (const eventStr of executeStreaming(request)) {
    reply.raw.write(eventStr);

    // done イベントから session_id を抽出
    if (eventStr.includes("event: done\n")) {
      try {
        const dataLine = eventStr.split("data: ")[1]?.split("\n")[0];
        if (dataLine) {
          const doneData = JSON.parse(dataLine);
          sessionIdFromDone = doneData.session_id ?? null;
        }
      } catch {
        // パースエラーは無視
      }
    }
  }

  // 4. S3 へワークスペース同期 + セッションファイル保存
  if (ws?.enabled && ws.s3_bucket) {
    try {
      await syncToS3({
        s3Bucket: ws.s3_bucket,
        s3Prefix: ws.s3_prefix,
        tenantId: ws.tenant_id,
        conversationId: ws.conversation_id,
        region: request.aws_region,
      });
    } catch (e) {
      logger.error({ msg: "ワークスペース S3 同期エラー（続行）", error: String(e) });
    }

    // セッションファイル保存
    const sid = sessionIdFromDone ?? request.session_id;
    if (sid) {
      try {
        await saveSessionFile({
          s3Bucket: ws.s3_bucket,
          s3Prefix: ws.s3_prefix,
          tenantId: ws.tenant_id,
          conversationId: ws.conversation_id,
          sessionId: sid,
          region: request.aws_region,
        });
      } catch (e) {
        logger.warn({ msg: "セッションファイル保存エラー（続行）", error: String(e) });
      }
    }
  }

  // 5. file_manifest イベントを末尾に追加
  const manifest = getFileManifest();
  const manifestEvent = `event: file_manifest\ndata: ${JSON.stringify({ files: manifest })}\n\n`;
  reply.raw.write(manifestEvent);

  reply.raw.end();
});

// =============================================================================
// GET /ping — AgentCore ヘルスチェック
// =============================================================================

app.get("/ping", async (_req, reply) => {
  reply.type("text/plain").send("ok");
});

// =============================================================================
// GET /health — 後方互換ヘルスチェック
// =============================================================================

app.get("/health", async (_req, reply) => {
  const response: HealthResponse = { status: "ok" };
  reply.send(response);
});

// =============================================================================
// スキルファイル書き出し
// =============================================================================

function writeSkillFiles(skillFiles: Record<string, string>): void {
  for (const [relativePath, b64Content] of Object.entries(skillFiles)) {
    const dest = path.join("/workspace", relativePath);
    const dir = path.dirname(dest);

    try {
      fs.mkdirSync(dir, { recursive: true });
      const data = Buffer.from(b64Content, "base64");
      fs.writeFileSync(dest, data);
      logger.debug({ msg: "スキルファイル書き出し", path: relativePath, bytes: data.length });
    } catch (e) {
      logger.error({ msg: "スキルファイル書き出しエラー", path: relativePath, error: String(e) });
    }
  }
}

// =============================================================================
// サーバー起動
// =============================================================================

async function main() {
  try {
    await app.listen({ port: AGENT_HTTP_PORT, host: "0.0.0.0" });
    logger.info({ msg: "ワークスペースエージェント起動（AgentCore モード）", port: AGENT_HTTP_PORT });
  } catch (err) {
    logger.error({ msg: "起動エラー", error: String(err) });
    process.exit(1);
  }
}

main();
