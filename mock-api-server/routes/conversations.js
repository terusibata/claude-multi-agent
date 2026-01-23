const express = require("express");
const multer = require("multer");
const store = require("../store/memory-store");
const {
  randomInt,
  randomBoolean,
  randomDelay,
  randomTitle,
  generateStreamingChunks,
  generatePresentFilesToolUse,
  generatePresentFilesToolResult,
  generateUsageStats,
  calculateCost,
  randomFileName,
  getMimeType,
} = require("../utils/random");

const router = express.Router({ mergeParams: true });
const upload = multer({ storage: multer.memoryStorage() });

// Helper to format conversation response
function formatConversation(c) {
  return {
    conversation_id: c.conversationId,
    session_id: c.sessionId,
    tenant_id: c.tenantId,
    user_id: c.userId,
    model_id: c.modelId,
    title: c.title,
    status: c.status,
    workspace_enabled: c.workspaceEnabled,
    created_at: c.createdAt,
    updated_at: c.updatedAt,
  };
}

// GET /api/tenants/:tenant_id/conversations - List conversations
router.get("/", (req, res) => {
  const { user_id, status, from_date, to_date, limit = 100, offset = 0 } = req.query;

  let conversations = store.getConversationsForTenant(req.params.tenant_id, {
    userId: user_id,
    status,
    fromDate: from_date,
    toDate: to_date,
  });

  conversations = conversations.slice(parseInt(offset), parseInt(offset) + parseInt(limit));
  res.json(conversations.map(formatConversation));
});

// POST /api/tenants/:tenant_id/conversations - Create conversation
router.post("/", (req, res) => {
  const tenantId = req.params.tenant_id;

  if (!store.getTenant(tenantId)) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Tenant not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const { user_id, model_id, workspace_enabled } = req.body;

  if (!user_id) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "user_id is required",
        details: [{ field: "user_id", message: "This field is required" }],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const conversation = store.createConversation(tenantId, {
    userId: user_id,
    modelId: model_id,
    workspaceEnabled: workspace_enabled,
  });

  res.status(201).json(formatConversation(conversation));
});

// GET /api/tenants/:tenant_id/conversations/:conversation_id - Get conversation
router.get("/:conversation_id", (req, res) => {
  const conversation = store.getConversation(req.params.tenant_id, req.params.conversation_id);

  if (!conversation) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Conversation not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatConversation(conversation));
});

// PUT /api/tenants/:tenant_id/conversations/:conversation_id - Update conversation
router.put("/:conversation_id", (req, res) => {
  const { title, status } = req.body;

  const conversation = store.updateConversation(req.params.tenant_id, req.params.conversation_id, {
    title,
    status,
  });

  if (!conversation) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Conversation not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatConversation(conversation));
});

// POST /api/tenants/:tenant_id/conversations/:conversation_id/archive - Archive conversation
router.post("/:conversation_id/archive", (req, res) => {
  const conversation = store.updateConversation(req.params.tenant_id, req.params.conversation_id, {
    status: "archived",
  });

  if (!conversation) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Conversation not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatConversation(conversation));
});

// DELETE /api/tenants/:tenant_id/conversations/:conversation_id - Delete conversation
router.delete("/:conversation_id", (req, res) => {
  const deleted = store.deleteConversation(req.params.tenant_id, req.params.conversation_id);

  if (!deleted) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Conversation not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.status(204).send();
});

// GET /api/tenants/:tenant_id/conversations/:conversation_id/messages - Get messages
router.get("/:conversation_id/messages", (req, res) => {
  const conversation = store.getConversation(req.params.tenant_id, req.params.conversation_id);

  if (!conversation) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Conversation not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const messages = store.getMessagesForConversation(req.params.conversation_id);

  res.json(
    messages.map((m) => ({
      message_id: m.messageId,
      conversation_id: m.conversationId,
      message_seq: m.messageSeq,
      message_type: m.messageType,
      message_subtype: m.messageSubtype,
      content: m.content,
      timestamp: m.timestamp,
    }))
  );
});

// POST /api/tenants/:tenant_id/conversations/:conversation_id/stream - Stream execution
router.post("/:conversation_id/stream", upload.array("files"), async (req, res) => {
  const tenantId = req.params.tenant_id;
  const conversationId = req.params.conversation_id;

  const conversation = store.getConversation(tenantId, conversationId);

  if (!conversation) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Conversation not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  // Parse request_data from form data
  let requestData;
  try {
    requestData = JSON.parse(req.body.request_data || "{}");
  } catch (e) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "Invalid request_data JSON",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  // Set up SSE
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");

  let seq = 0;
  const startTime = Date.now();
  const sessionId = conversation.sessionId;

  // Available tools list (mock)
  const availableTools = [
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Glob",
    "Grep",
    "mcp__file-presentation__present_files",
  ];

  const sendEvent = (eventType, data) => {
    seq++;
    const event = {
      seq,
      timestamp: new Date().toISOString(),
      event: eventType, // フロントエンドでイベントタイプを判別しやすくするため
      ...data,
    };
    res.write(`event: ${eventType}\n`);
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  };

  // init event (was session_start)
  sendEvent("init", {
    session_id: sessionId,
    tools: availableTools,
    model: conversation.modelId || "claude-sonnet-4",
    conversation_id: conversationId,
  });

  await randomDelay(100, 300);

  // Random thinking event (30% chance)
  if (randomBoolean(0.3)) {
    sendEvent("thinking", {
      content: "Analyzing the request and preparing response...",
    });
    await randomDelay(200, 500);
  }

  // Generate response text
  const chunks = generateStreamingChunks();
  let fullText = chunks.join("");

  await randomDelay(200, 500);

  // assistant event with content_blocks (was text_delta)
  sendEvent("assistant", {
    content_blocks: [
      {
        type: "text",
        text: fullText,
      },
    ],
  });

  // Random tool use (50% chance for present_files)
  let toolUsed = false;
  if (randomBoolean(0.5)) {
    toolUsed = true;
    const toolUse = generatePresentFilesToolUse();

    // tool_call event (was tool_use)
    sendEvent("tool_call", {
      tool_use_id: toolUse.toolUseId,
      tool_name: toolUse.toolName,
      input: toolUse.input,
      summary: `Presenting ${toolUse.input.files.length} file(s) to workspace`,
    });

    await randomDelay(300, 800);

    // Store files in workspace if workspace enabled
    if (conversation.workspaceEnabled) {
      for (const file of toolUse.input.files) {
        const filename = file.filePath.split("/").pop();
        store.addWorkspaceFile(conversationId, {
          filePath: file.filePath,
          originalName: filename,
          fileSize: randomInt(1000, 50000),
          mimeType: getMimeType(filename),
          source: "ai_created",
          isPresented: true,
          description: file.description,
        });
      }
    }

    const toolResult = generatePresentFilesToolResult(toolUse.toolUseId, toolUse.input.files);

    // tool_result event with updated structure
    sendEvent("tool_result", {
      tool_use_id: toolResult.toolUseId,
      tool_name: toolUse.toolName,
      status: "completed",
      content: JSON.stringify(toolResult.output).substring(0, 500),
      is_error: false,
    });

    // Log tool usage
    store.addToolLog({
      sessionId,
      conversationId,
      toolName: toolUse.toolName,
      toolUseId: toolUse.toolUseId,
      toolInput: toolUse.input,
      toolOutput: toolResult.output,
      status: "success",
      executionTimeMs: randomInt(100, 500),
    });

    await randomDelay(100, 200);

    // Additional text after tool use
    const additionalText = "\n\nI've prepared the files for you. You can download them from the workspace.";
    sendEvent("assistant", {
      content_blocks: [
        {
          type: "text",
          text: additionalText,
        },
      ],
    });
    fullText += additionalText;
  }

  // Generate usage stats and calculate cost
  const usage = generateUsageStats();
  const model = store.getModel(conversation.modelId);
  const costUsd = calculateCost(usage, model);
  const durationMs = Date.now() - startTime;

  // Store usage log
  store.addUsageLog({
    tenantId,
    userId: requestData.executor?.user_id || "unknown",
    modelId: conversation.modelId,
    sessionId,
    conversationId,
    inputTokens: usage.inputTokens,
    outputTokens: usage.outputTokens,
    cacheCreation5mTokens: usage.cacheCreation5mTokens,
    cacheCreation1hTokens: usage.cacheCreation1hTokens,
    cacheReadTokens: usage.cacheReadTokens,
    totalTokens: usage.totalTokens,
    costUsd,
  });

  // Store messages
  store.addMessage(conversationId, {
    messageType: "user",
    content: { text: requestData.user_input },
  });

  store.addMessage(conversationId, {
    messageType: "assistant",
    content: { text: fullText },
  });

  // Update conversation title if first message
  const messages = store.getMessagesForConversation(conversationId);
  let generatedTitle = null;
  if (messages.length <= 2) {
    generatedTitle = randomTitle();
    store.updateConversation(tenantId, conversationId, {
      title: generatedTitle,
    });

    // title event
    sendEvent("title", {
      title: generatedTitle,
    });
  }

  // done event (was result)
  sendEvent("done", {
    status: "success",
    result: fullText,
    is_error: false,
    errors: null,
    usage: {
      input_tokens: usage.inputTokens,
      output_tokens: usage.outputTokens,
      cache_creation_5m_tokens: usage.cacheCreation5mTokens,
      cache_creation_1h_tokens: usage.cacheCreation1hTokens,
      cache_read_tokens: usage.cacheReadTokens,
      total_tokens: usage.totalTokens,
    },
    cost_usd: costUsd,
    turn_count: toolUsed ? 2 : 1,
    duration_ms: durationMs,
    session_id: sessionId,
  });

  res.end();
});

module.exports = router;
