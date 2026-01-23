const express = require("express");
const store = require("../store/memory-store");
const {
  randomInt,
  randomBoolean,
  randomDelay,
  randomTitle,
  generateStreamingChunks,
  generateUsageStats,
  calculateCost,
} = require("../utils/random");

const router = express.Router({ mergeParams: true });

// Helper to format simple chat response
function formatSimpleChat(c) {
  return {
    chat_id: c.chatId,
    tenant_id: c.tenantId,
    user_id: c.userId,
    model_id: c.modelId,
    application_type: c.applicationType,
    system_prompt: c.systemPrompt,
    title: c.title,
    status: c.status,
    created_at: c.createdAt,
    updated_at: c.updatedAt,
  };
}

// Helper to format simple chat message
function formatSimpleChatMessage(m) {
  return {
    message_id: m.messageId,
    chat_id: m.chatId,
    message_seq: m.messageSeq,
    role: m.role,
    content: m.content,
    created_at: m.createdAt,
  };
}

// GET /api/tenants/:tenant_id/simple-chats - List simple chats
router.get("/", (req, res) => {
  const { user_id, application_type, status, limit = 100, offset = 0 } = req.query;

  let chats = store.getSimpleChatsForTenant(req.params.tenant_id, {
    userId: user_id,
    applicationType: application_type,
    status,
  });

  const total = chats.length;
  chats = chats.slice(parseInt(offset), parseInt(offset) + parseInt(limit));

  res.json({
    items: chats.map(formatSimpleChat),
    total,
    limit: parseInt(limit),
    offset: parseInt(offset),
  });
});

// GET /api/tenants/:tenant_id/simple-chats/:chat_id - Get simple chat detail
router.get("/:chat_id", (req, res) => {
  const chat = store.getSimpleChat(req.params.tenant_id, req.params.chat_id);

  if (!chat) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Simple chat not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const messages = store.getSimpleChatMessages(req.params.chat_id);

  res.json({
    ...formatSimpleChat(chat),
    messages: messages.map(formatSimpleChatMessage),
  });
});

// POST /api/tenants/:tenant_id/simple-chats/:chat_id/archive - Archive simple chat
router.post("/:chat_id/archive", (req, res) => {
  const chat = store.updateSimpleChat(req.params.tenant_id, req.params.chat_id, {
    status: "archived",
  });

  if (!chat) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Simple chat not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatSimpleChat(chat));
});

// DELETE /api/tenants/:tenant_id/simple-chats/:chat_id - Delete simple chat
router.delete("/:chat_id", (req, res) => {
  const deleted = store.deleteSimpleChat(req.params.tenant_id, req.params.chat_id);

  if (!deleted) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Simple chat not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.status(204).send();
});

// POST /api/tenants/:tenant_id/simple-chats/stream - Stream execution
router.post("/stream", async (req, res) => {
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

  const { chat_id, user_id, application_type, system_prompt, model_id, message } = req.body;

  if (!message) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "message is required",
        details: [{ field: "message", message: "This field is required" }],
        timestamp: new Date().toISOString(),
      },
    });
  }

  let chat;
  let isNewChat = false;

  if (chat_id) {
    // Existing chat
    chat = store.getSimpleChat(tenantId, chat_id);
    if (!chat) {
      return res.status(404).json({
        error: {
          code: "NOT_FOUND",
          message: "Simple chat not found",
          details: [],
          timestamp: new Date().toISOString(),
        },
      });
    }
  } else {
    // New chat - validate required fields
    if (!user_id || !application_type || !system_prompt || !model_id) {
      return res.status(422).json({
        error: {
          code: "VALIDATION_ERROR",
          message: "user_id, application_type, system_prompt, and model_id are required for new chat",
          details: [],
          timestamp: new Date().toISOString(),
        },
      });
    }

    chat = store.createSimpleChat(tenantId, {
      userId: user_id,
      applicationType: application_type,
      systemPrompt: system_prompt,
      modelId: model_id,
    });
    isNewChat = true;
  }

  // Set up SSE
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");

  if (isNewChat) {
    res.setHeader("X-Chat-ID", chat.chatId);
  }

  let seq = 0;

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

  // Store user message
  store.addSimpleChatMessage(chat.chatId, "user", message);

  await randomDelay(100, 300);

  // Stream text chunks (AWS Bedrock supports streaming)
  const chunks = generateStreamingChunks();
  let fullText = "";

  for (const chunk of chunks) {
    sendEvent("text_delta", { content: chunk });
    fullText += chunk;
    await randomDelay(30, 100);
  }

  // Store assistant message
  store.addSimpleChatMessage(chat.chatId, "assistant", fullText);

  // Generate title for new chat
  let title = null;
  if (isNewChat) {
    title = randomTitle();
    store.updateSimpleChat(tenantId, chat.chatId, { title });
  }

  // Generate usage stats
  const usage = generateUsageStats();
  const model = store.getModel(chat.modelId);
  const costUsd = calculateCost(usage, model);

  // Store usage log
  store.addUsageLog({
    tenantId,
    userId: chat.userId,
    modelId: chat.modelId,
    sessionId: null,
    conversationId: null,
    inputTokens: usage.inputTokens,
    outputTokens: usage.outputTokens,
    cacheCreation5mTokens: 0,
    cacheCreation1hTokens: 0,
    cacheReadTokens: 0,
    totalTokens: usage.totalTokens,
    costUsd,
  });

  // Done event
  const doneData = {
    usage: {
      input_tokens: usage.inputTokens,
      output_tokens: usage.outputTokens,
      total_tokens: usage.totalTokens,
    },
    cost_usd: costUsd,
  };

  if (isNewChat) {
    doneData.title = title;
  }

  sendEvent("done", doneData);

  res.end();
});

module.exports = router;
