/**
 * エージェントコンテナ内 S3 同期モジュール (TypeScript)
 *
 * AgentCore の microVM 内で動作し、ホスト側の docker exec ベースの同期を代替する。
 * @aws-sdk/client-s3 を使用し、AgentCore の実行ロール(Execution Role)で S3 にアクセスする。
 *
 * 機能:
 *   - restoreFromS3(): S3 → /workspace にファイルをダウンロード（セッション開始時）
 *   - syncToS3(): /workspace → S3 に全ファイルをアップロード（Turn完了後）
 *   - restoreSessionFile(): S3 の _sdk_session/{id}.jsonl → SDK projects dir に復元
 *   - saveSessionFile(): SDK セッションファイルを S3 に保存
 *   - getFileManifest(): ワークスペース内の全ファイル一覧を返却
 */
import {
  S3Client,
  GetObjectCommand,
  PutObjectCommand,
  ListObjectsV2Command,
} from "@aws-sdk/client-s3";
import * as fs from "node:fs";
import * as path from "node:path";
import { createLogger } from "./logger.js";

const logger = createLogger("agent-file-sync");

// 同期対象から除外するディレクトリ名
const EXCLUDED_DIR_NAMES = new Set([
  "__pycache__", ".git", "node_modules", ".venv", "venv",
  ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
  ".eggs", ".egg-info",
]);

// 同期対象から除外する拡張子
const EXCLUDED_EXTENSIONS = new Set([".pyc", ".pyo", ".DS_Store"]);

// 予約プレフィックス
const RESERVED_PREFIXES = ["_sdk_session/"];

// SDK セッションファイルのベースパス
const SDK_PROJECTS_DIR = "/home/appuser/.claude/projects/-workspace";

// ワークスペースルート
const WORKSPACE_ROOT = "/workspace";

// =============================================================================
// ユーティリティ
// =============================================================================

function shouldExclude(filePath: string): boolean {
  const segments = filePath.split("/");
  for (const seg of segments.slice(0, -1)) {
    if (EXCLUDED_DIR_NAMES.has(seg)) return true;
  }
  const filename = segments[segments.length - 1] ?? "";
  for (const ext of EXCLUDED_EXTENSIONS) {
    if (filename.endsWith(ext) || filename === ext.replace(/^\./, "")) return true;
  }
  return false;
}

function isReservedPath(filePath: string): boolean {
  return RESERVED_PREFIXES.some(
    (prefix) => filePath.startsWith(prefix) || filePath === prefix.replace(/\/$/, ""),
  );
}

function getS3Client(region?: string): S3Client {
  return new S3Client({
    region: region || process.env.AWS_REGION || "us-west-2",
  });
}

/** ディレクトリを再帰的に走査してファイル一覧を返す */
function walkDir(dir: string): string[] {
  const result: string[] = [];
  if (!fs.existsSync(dir)) return result;

  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      result.push(...walkDir(fullPath));
    } else if (entry.isFile()) {
      result.push(fullPath);
    }
  }
  return result;
}

// =============================================================================
// S3 同期パラメータ
// =============================================================================

interface S3SyncParams {
  s3Bucket: string;
  s3Prefix: string;
  tenantId: string;
  conversationId: string;
  region?: string;
}

interface S3SessionParams extends S3SyncParams {
  sessionId: string;
}

// =============================================================================
// パブリック API
// =============================================================================

/**
 * S3 → /workspace にファイルをダウンロード（セッション開始時）
 */
export async function restoreFromS3(params: S3SyncParams): Promise<number> {
  const s3 = getS3Client(params.region);
  const prefix = `${params.s3Prefix}${params.tenantId}/${params.conversationId}/`;
  let restored = 0;

  let continuationToken: string | undefined;
  do {
    const response = await s3.send(
      new ListObjectsV2Command({
        Bucket: params.s3Bucket,
        Prefix: prefix,
        ContinuationToken: continuationToken,
      }),
    );

    for (const obj of response.Contents ?? []) {
      const s3Key = obj.Key!;
      const relative = s3Key.slice(prefix.length);

      if (isReservedPath(relative) || shouldExclude(relative)) continue;

      const localPath = path.join(WORKSPACE_ROOT, relative);
      const dir = path.dirname(localPath);

      try {
        fs.mkdirSync(dir, { recursive: true });
        const getResp = await s3.send(
          new GetObjectCommand({ Bucket: params.s3Bucket, Key: s3Key }),
        );
        const body = await getResp.Body!.transformToByteArray();
        fs.writeFileSync(localPath, body);
        restored++;
      } catch (e) {
        logger.error({ msg: "S3ダウンロードエラー", s3Key, localPath, error: String(e) });
      }
    }

    continuationToken = response.NextContinuationToken;
  } while (continuationToken);

  logger.info({ msg: "S3→ワークスペース復元完了", files: restored });
  return restored;
}

/**
 * /workspace → S3 に全ファイルをアップロード（Turn完了後）
 */
export async function syncToS3(params: S3SyncParams): Promise<number> {
  const s3 = getS3Client(params.region);
  const prefix = `${params.s3Prefix}${params.tenantId}/${params.conversationId}/`;
  let synced = 0;

  const files = walkDir(WORKSPACE_ROOT);
  for (const filePath of files) {
    const relative = path.relative(WORKSPACE_ROOT, filePath);
    if (isReservedPath(relative) || shouldExclude(relative)) continue;

    const s3Key = `${prefix}${relative}`;
    try {
      const content = fs.readFileSync(filePath);
      await s3.send(
        new PutObjectCommand({ Bucket: params.s3Bucket, Key: s3Key, Body: content }),
      );
      synced++;
    } catch (e) {
      logger.error({ msg: "S3アップロードエラー", filePath, s3Key, error: String(e) });
    }
  }

  logger.info({ msg: "ワークスペース→S3同期完了", files: synced });
  return synced;
}

/**
 * S3 の _sdk_session/{session_id}.jsonl → SDK projects dir に復元
 */
export async function restoreSessionFile(params: S3SessionParams): Promise<boolean> {
  const s3 = getS3Client(params.region);
  const s3Key = `${params.s3Prefix}${params.tenantId}/${params.conversationId}/_sdk_session/${params.sessionId}.jsonl`;
  const destPath = path.join(SDK_PROJECTS_DIR, `${params.sessionId}.jsonl`);

  try {
    fs.mkdirSync(path.dirname(destPath), { recursive: true });
    const response = await s3.send(
      new GetObjectCommand({ Bucket: params.s3Bucket, Key: s3Key }),
    );
    const body = await response.Body!.transformToByteArray();
    fs.writeFileSync(destPath, body);
    logger.info({ msg: "セッションファイル復元完了", sessionId: params.sessionId, bytes: body.length });
    return true;
  } catch (e: unknown) {
    const errorCode = (e as { name?: string }).name;
    if (errorCode === "NoSuchKey" || errorCode === "NotFound") {
      logger.debug({ msg: "S3にセッションファイルなし（新規セッション）", sessionId: params.sessionId });
    } else {
      logger.error({ msg: "セッションファイル復元エラー", error: String(e) });
    }
    return false;
  }
}

/**
 * SDK セッションファイルを S3 に保存
 */
export async function saveSessionFile(params: S3SessionParams): Promise<boolean> {
  const s3 = getS3Client(params.region);
  const sessionPath = path.join(SDK_PROJECTS_DIR, `${params.sessionId}.jsonl`);

  if (!fs.existsSync(sessionPath)) {
    logger.debug({ msg: "セッションファイル未検出（スキップ）", sessionId: params.sessionId });
    return false;
  }

  const s3Key = `${params.s3Prefix}${params.tenantId}/${params.conversationId}/_sdk_session/${params.sessionId}.jsonl`;
  try {
    const content = fs.readFileSync(sessionPath);
    await s3.send(
      new PutObjectCommand({ Bucket: params.s3Bucket, Key: s3Key, Body: content }),
    );
    logger.info({ msg: "セッションファイルS3保存完了", sessionId: params.sessionId, bytes: content.length });
    return true;
  } catch (e) {
    logger.error({ msg: "セッションファイル保存エラー", error: String(e) });
    return false;
  }
}

/**
 * ワークスペース内の全ファイル一覧（パス+サイズ）を返却
 */
export function getFileManifest(): Array<{ path: string; size: number }> {
  const files = walkDir(WORKSPACE_ROOT);
  const manifest: Array<{ path: string; size: number }> = [];

  for (const filePath of files) {
    const relative = path.relative(WORKSPACE_ROOT, filePath);
    if (isReservedPath(relative) || shouldExclude(relative)) continue;

    try {
      const stat = fs.statSync(filePath);
      manifest.push({ path: relative, size: stat.size });
    } catch {
      manifest.push({ path: relative, size: 0 });
    }
  }

  return manifest;
}
