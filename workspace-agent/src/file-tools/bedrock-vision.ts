/**
 * Bedrock Vision クライアント
 *
 * 画像バッファ + プロンプトを受け取り、Bedrock Converse API で画像分析テキストを返す。
 * 使用量アキュムレータを内蔵し、SDK done イベントで modelUsageData にマージできる。
 */
import {
  BedrockRuntimeClient,
  ConverseCommand,
  type ImageFormat,
  type ContentBlock as ConverseContentBlock,
} from "@aws-sdk/client-bedrock-runtime";
import { createLogger } from "../logger.js";

const logger = createLogger("bedrock-vision");

// Haiku: コスト効率的なサブ処理タスク向け
const DEFAULT_VISION_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0";

/** Vision API で対応する画像フォーマット */
export const SUPPORTED_VISION_FORMATS = new Set<string>([
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
]);

// =============================================================================
// 使用量アキュムレータ
// =============================================================================

let accumulatedUsage = { inputTokens: 0, outputTokens: 0 };

/**
 * 蓄積された Vision API 使用量を取得し、アキュムレータをリセットする。
 * SDK ResultMessage 処理時に呼び出す。
 */
export function getAndResetVisionUsage(): {
  inputTokens: number;
  outputTokens: number;
} {
  const usage = { ...accumulatedUsage };
  accumulatedUsage = { inputTokens: 0, outputTokens: 0 };
  return usage;
}

/**
 * 現在設定されている Vision モデル ID を返す。
 * done イベントで model_usage のキーに使用する。
 */
export function getVisionModelId(): string {
  return process.env.VISION_MODEL_ID || DEFAULT_VISION_MODEL;
}

// =============================================================================
// Converse API 呼び出し
// =============================================================================

export interface ImageInput {
  buffer: Buffer;
  format: ImageFormat;
}

export interface VisionResult {
  text: string;
  inputTokens: number;
  outputTokens: number;
}

/**
 * Bedrock Converse API で画像を分析し、テキスト結果を返す。
 * 複数画像を1回の API 呼び出しで分析可能（最大20枚）。
 */
export async function analyzeImagesWithBedrock(params: {
  images: ImageInput[];
  prompt: string;
  region?: string;
}): Promise<VisionResult> {
  const modelId = getVisionModelId();
  const region = params.region || process.env.AWS_REGION || "ap-northeast-1";

  const client = new BedrockRuntimeClient({ region });

  // content ブロックを構築: 画像ブロック + テキストプロンプト
  const contentBlocks: ConverseContentBlock[] = [];
  for (const img of params.images) {
    contentBlocks.push({
      image: {
        format: img.format,
        source: { bytes: img.buffer },
      },
    });
  }
  contentBlocks.push({ text: params.prompt });

  const command = new ConverseCommand({
    modelId,
    messages: [
      {
        role: "user",
        content: contentBlocks,
      },
    ],
    system: [
      {
        text: "画像を分析し、ユーザーの質問に対して詳細かつ正確に回答してください。テキストが含まれる場合は可能な限り正確に読み取ってください。",
      },
    ],
    inferenceConfig: {
      maxTokens: 4096,
      temperature: 0.3,
    },
  });

  logger.info({
    msg: "Bedrock Vision API 呼び出し",
    model: modelId,
    region,
    imageCount: params.images.length,
  });

  const response = await client.send(command);

  // レスポンスからテキスト抽出
  const outputContent = response.output?.message?.content || [];
  const text = outputContent.map((block) => block.text || "").join("");

  // 使用量を取得・蓄積
  const inputTokens = response.usage?.inputTokens ?? 0;
  const outputTokens = response.usage?.outputTokens ?? 0;

  accumulatedUsage.inputTokens += inputTokens;
  accumulatedUsage.outputTokens += outputTokens;

  logger.info({
    msg: "Bedrock Vision API 完了",
    inputTokens,
    outputTokens,
    responseLength: text.length,
  });

  return { text, inputTokens, outputTokens };
}
