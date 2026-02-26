/**
 * 構造化ロガー（pino ベース）
 */
import pino from "pino";

const rootLogger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  timestamp: pino.stdTimeFunctions.isoTime,
});

export function createLogger(name: string) {
  return rootLogger.child({ module: name });
}
