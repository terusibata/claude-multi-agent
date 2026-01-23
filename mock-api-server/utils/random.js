// Utility functions for generating random mock data

const adjectives = ["creative", "innovative", "helpful", "efficient", "insightful", "comprehensive", "detailed"];
const nouns = ["analysis", "solution", "approach", "strategy", "implementation", "response", "summary"];
const topics = ["data processing", "user experience", "system optimization", "workflow automation", "report generation"];

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randomFloat(min, max, decimals = 2) {
  return parseFloat((Math.random() * (max - min) + min).toFixed(decimals));
}

function randomChoice(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randomBoolean(probability = 0.5) {
  return Math.random() < probability;
}

function randomTitle() {
  return `${randomChoice(adjectives).charAt(0).toUpperCase() + randomChoice(adjectives).slice(1)} ${randomChoice(nouns)} for ${randomChoice(topics)}`;
}

function randomDelay(minMs, maxMs) {
  return new Promise((resolve) => setTimeout(resolve, randomInt(minMs, maxMs)));
}

// Generate random streaming text chunks
const streamingPhrases = [
  "Let me analyze this request for you.",
  "I'll help you with that.",
  "Based on my analysis,",
  "Here's what I found:",
  "The results show that",
  "I've completed the task.",
  "This is an interesting problem.",
  "Let me break this down:",
  "After careful consideration,",
  "I've processed your request.",
];

const fileNames = [
  "report.pdf",
  "analysis.xlsx",
  "summary.docx",
  "data.json",
  "output.csv",
  "results.txt",
  "chart.png",
  "diagram.svg",
];

const mimeTypes = {
  pdf: "application/pdf",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  json: "application/json",
  csv: "text/csv",
  txt: "text/plain",
  png: "image/png",
  svg: "image/svg+xml",
};

function randomFileName() {
  return randomChoice(fileNames);
}

function getMimeType(filename) {
  const ext = filename.split(".").pop();
  return mimeTypes[ext] || "application/octet-stream";
}

function generateRandomText(wordCount = 50) {
  const words = [
    "the", "a", "is", "in", "it", "of", "to", "and", "for", "on",
    "with", "as", "this", "that", "by", "from", "or", "an", "be", "was",
    "data", "analysis", "system", "process", "result", "output", "input",
    "user", "request", "response", "task", "action", "function", "method",
    "value", "key", "file", "document", "report", "summary", "detail",
  ];
  const result = [];
  for (let i = 0; i < wordCount; i++) {
    result.push(randomChoice(words));
  }
  return result.join(" ");
}

function generateStreamingChunks() {
  const chunks = [];
  const numPhrases = randomInt(3, 6);

  for (let i = 0; i < numPhrases; i++) {
    chunks.push(randomChoice(streamingPhrases) + " ");
  }

  // Add some random generated text
  chunks.push(generateRandomText(randomInt(20, 50)));

  return chunks;
}

// Generate tool use event for present_files
function generatePresentFilesToolUse() {
  const numFiles = randomInt(1, 3);
  const files = [];

  for (let i = 0; i < numFiles; i++) {
    const filename = randomFileName();
    files.push({
      filePath: `/workspace/output/${filename}`,
      description: `Generated ${filename.split(".")[0]} file`,
    });
  }

  return {
    toolName: "mcp__file-presentation__present_files",
    toolUseId: `toolu_${Date.now()}_${randomInt(1000, 9999)}`,
    input: {
      files: files,
    },
  };
}

// Generate tool result for present_files
function generatePresentFilesToolResult(toolUseId, files) {
  return {
    toolUseId: toolUseId,
    output: {
      success: true,
      presentedFiles: files.map((f) => ({
        filePath: f.filePath,
        presented: true,
      })),
    },
  };
}

// Generate random usage stats
function generateUsageStats() {
  const inputTokens = randomInt(100, 2000);
  const outputTokens = randomInt(50, 1500);
  const cacheCreation5mTokens = randomBoolean(0.3) ? randomInt(0, 500) : 0;
  const cacheCreation1hTokens = randomBoolean(0.2) ? randomInt(0, 300) : 0;
  const cacheReadTokens = randomBoolean(0.4) ? randomInt(0, 800) : 0;

  return {
    inputTokens,
    outputTokens,
    cacheCreation5mTokens,
    cacheCreation1hTokens,
    cacheReadTokens,
    totalTokens: inputTokens + outputTokens,
  };
}

// Calculate cost based on usage and model
function calculateCost(usage, model) {
  const inputCost = (usage.inputTokens / 1000) * parseFloat(model?.inputTokenPrice || "0.003");
  const outputCost = (usage.outputTokens / 1000) * parseFloat(model?.outputTokenPrice || "0.015");
  const cache5mCost = (usage.cacheCreation5mTokens / 1000) * parseFloat(model?.cacheCreation5mPrice || "0.00375");
  const cache1hCost = (usage.cacheCreation1hTokens / 1000) * parseFloat(model?.cacheCreation1hPrice || "0.006");
  const cacheReadCost = (usage.cacheReadTokens / 1000) * parseFloat(model?.cacheReadPrice || "0.0003");

  return (inputCost + outputCost + cache5mCost + cache1hCost + cacheReadCost).toFixed(6);
}

module.exports = {
  randomInt,
  randomFloat,
  randomChoice,
  randomBoolean,
  randomTitle,
  randomDelay,
  randomFileName,
  getMimeType,
  generateRandomText,
  generateStreamingChunks,
  generatePresentFilesToolUse,
  generatePresentFilesToolResult,
  generateUsageStats,
  calculateCost,
};
