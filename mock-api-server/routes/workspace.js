const express = require("express");
const store = require("../store/memory-store");
const { randomInt, randomFileName, getMimeType } = require("../utils/random");

const router = express.Router({ mergeParams: true });

// Helper to format workspace file
function formatWorkspaceFile(f) {
  return {
    file_id: f.fileId,
    file_path: f.filePath,
    original_name: f.originalName,
    file_size: f.fileSize,
    mime_type: f.mimeType,
    version: f.version,
    source: f.source,
    is_presented: f.isPresented,
    checksum: f.checksum,
    description: f.description,
    created_at: f.createdAt,
    updated_at: f.updatedAt,
  };
}

// GET /api/tenants/:tenant_id/conversations/:conversation_id/files - List files
router.get("/conversations/:conversation_id/files", (req, res) => {
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

  const files = store.getWorkspaceFiles(conversationId);
  const totalSize = files.reduce((sum, f) => sum + f.fileSize, 0);

  res.json({
    conversation_id: conversationId,
    files: files.map(formatWorkspaceFile),
    total_count: files.length,
    total_size: totalSize,
  });
});

// GET /api/tenants/:tenant_id/conversations/:conversation_id/files/download - Download file
router.get("/conversations/:conversation_id/files/download", (req, res) => {
  const tenantId = req.params.tenant_id;
  const conversationId = req.params.conversation_id;
  const { path: filePath } = req.query;

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

  if (!filePath) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "path query parameter is required",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const files = store.getWorkspaceFiles(conversationId);
  const file = files.find((f) => f.filePath === filePath);

  if (!file) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "File not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  // Generate mock file content
  const mimeType = file.mimeType || "application/octet-stream";
  const filename = file.originalName;

  res.setHeader("Content-Type", mimeType);
  res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);

  // Send mock content based on file type
  if (mimeType.startsWith("text/") || mimeType === "application/json") {
    res.send(`Mock content for file: ${filename}\n\nThis is simulated file content for testing purposes.\n`);
  } else {
    // Send some random bytes for binary files
    const buffer = Buffer.alloc(file.fileSize || 1024);
    for (let i = 0; i < buffer.length; i++) {
      buffer[i] = randomInt(0, 255);
    }
    res.send(buffer);
  }
});

// GET /api/tenants/:tenant_id/conversations/:conversation_id/files/presented - Get presented files
router.get("/conversations/:conversation_id/files/presented", (req, res) => {
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

  const files = store.getWorkspaceFiles(conversationId);
  const presentedFiles = files.filter((f) => f.isPresented);

  res.json({
    conversation_id: conversationId,
    files: presentedFiles.map(formatWorkspaceFile),
  });
});

module.exports = router;
