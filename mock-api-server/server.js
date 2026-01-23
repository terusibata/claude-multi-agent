const express = require("express");
const cors = require("cors");
const { v4: uuidv4 } = require("uuid");

const { authMiddleware, optionalAuth } = require("./middleware/auth");

// Routes
const healthRoutes = require("./routes/health");
const tenantsRoutes = require("./routes/tenants");
const modelsRoutes = require("./routes/models");
const skillsRoutes = require("./routes/skills");
const mcpServersRoutes = require("./routes/mcp-servers");
const conversationsRoutes = require("./routes/conversations");
const usageRoutes = require("./routes/usage");
const workspaceRoutes = require("./routes/workspace");
const simpleChatsRoutes = require("./routes/simple-chats");

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Request ID middleware
app.use((req, res, next) => {
  req.requestId = uuidv4();
  res.setHeader("X-Request-ID", req.requestId);
  next();
});

// Logging middleware
app.use((req, res, next) => {
  const start = Date.now();
  res.on("finish", () => {
    const duration = Date.now() - start;
    console.log(`${new Date().toISOString()} ${req.method} ${req.path} ${res.statusCode} ${duration}ms`);
  });
  next();
});

// Root endpoint
app.get("/", (req, res) => {
  res.json({
    name: "Claude Multi-Agent Mock API",
    version: "1.0.0-mock",
    docs_url: "/docs",
  });
});

// Health routes (no auth required)
app.use("/health", healthRoutes);

// API routes (auth required)
app.use("/api/tenants", authMiddleware, tenantsRoutes);
app.use("/api/models", authMiddleware, modelsRoutes);

// Tenant-scoped routes
app.use("/api/tenants/:tenant_id/skills", authMiddleware, skillsRoutes);
app.use("/api/tenants/:tenant_id/mcp-servers", authMiddleware, mcpServersRoutes);
app.use("/api/tenants/:tenant_id/conversations", authMiddleware, conversationsRoutes);
app.use("/api/tenants/:tenant_id", authMiddleware, usageRoutes);
app.use("/api/tenants/:tenant_id", authMiddleware, workspaceRoutes);
app.use("/api/tenants/:tenant_id/simple-chats", authMiddleware, simpleChatsRoutes);

// 404 handler
app.use((req, res) => {
  res.status(404).json({
    error: {
      code: "NOT_FOUND",
      message: `Route ${req.method} ${req.path} not found`,
      details: [],
      request_id: req.requestId,
      timestamp: new Date().toISOString(),
    },
  });
});

// Error handler
app.use((err, req, res, next) => {
  console.error(`Error: ${err.message}`);
  console.error(err.stack);

  res.status(err.status || 500).json({
    error: {
      code: err.code || "INTERNAL_ERROR",
      message: err.message || "An unexpected error occurred",
      details: [],
      request_id: req.requestId,
      timestamp: new Date().toISOString(),
    },
  });
});

// Start server
app.listen(PORT, () => {
  console.log(`
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   Claude Multi-Agent Mock API Server                          ║
║                                                               ║
║   Server running at: http://localhost:${PORT}                    ║
║                                                               ║
║   Endpoints:                                                  ║
║   - GET  /health                    Health check              ║
║   - GET  /api/tenants               List tenants              ║
║   - GET  /api/models                List models               ║
║   - POST /api/tenants/:id/conversations/:id/stream            ║
║   - POST /api/tenants/:id/simple-chats/stream                 ║
║                                                               ║
║   Authentication:                                             ║
║   - Header: X-API-Key: <any-value>                            ║
║   - Header: Authorization: Bearer <any-value>                 ║
║                                                               ║
║   Initial Data:                                               ║
║   - Tenant: default-tenant                                    ║
║   - Model: global.anthropic.claude-sonnet4-5-20250929-v1:0    ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
  `);
});

module.exports = app;
