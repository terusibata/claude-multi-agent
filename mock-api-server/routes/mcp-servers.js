const express = require("express");
const store = require("../store/memory-store");

const router = express.Router({ mergeParams: true });

// Helper to format MCP server response
function formatMcpServer(s) {
  return {
    mcp_server_id: s.mcpServerId,
    tenant_id: s.tenantId,
    name: s.name,
    display_name: s.displayName,
    type: s.type,
    url: s.url,
    command: s.command,
    args: s.args,
    env: s.env,
    headers_template: s.headersTemplate,
    allowed_tools: s.allowedTools,
    tools: s.tools,
    description: s.description,
    openapi_spec: s.openapiSpec,
    openapi_base_url: s.openapiBaseUrl,
    status: s.status,
    created_at: s.createdAt,
    updated_at: s.updatedAt,
  };
}

// GET /api/tenants/:tenant_id/mcp-servers - List MCP servers
router.get("/", (req, res) => {
  const { status } = req.query;
  const servers = store.getMcpServersForTenant(req.params.tenant_id, status);
  res.json(servers.map(formatMcpServer));
});

// POST /api/tenants/:tenant_id/mcp-servers - Create MCP server
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

  const {
    name,
    display_name,
    type,
    url,
    command,
    args,
    env,
    headers_template,
    allowed_tools,
    tools,
    description,
    openapi_spec,
    openapi_base_url,
  } = req.body;

  if (!name || !type) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "name and type are required",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const server = store.createMcpServer(tenantId, {
    name,
    displayName: display_name,
    type,
    url,
    command,
    args,
    env,
    headersTemplate: headers_template,
    allowedTools: allowed_tools,
    tools,
    description,
    openapiSpec: openapi_spec,
    openapiBaseUrl: openapi_base_url,
  });

  res.status(201).json(formatMcpServer(server));
});

// GET /api/tenants/:tenant_id/mcp-servers/builtin - List builtin servers
router.get("/builtin", (req, res) => {
  // Return mock builtin servers
  res.json([
    {
      mcp_server_id: "builtin-file-presentation",
      tenant_id: null,
      name: "file-presentation",
      display_name: "File Presentation",
      type: "builtin",
      url: null,
      command: null,
      args: [],
      env: {},
      headers_template: {},
      allowed_tools: [],
      tools: [
        {
          name: "present_files",
          description: "Present files to the user for download",
          input_schema: {
            type: "object",
            properties: {
              files: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    filePath: { type: "string" },
                    description: { type: "string" },
                  },
                  required: ["filePath"],
                },
              },
            },
            required: ["files"],
          },
        },
      ],
      description: "Built-in file presentation server",
      openapi_spec: null,
      openapi_base_url: null,
      status: "active",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ]);
});

// GET /api/tenants/:tenant_id/mcp-servers/:server_id - Get MCP server
router.get("/:server_id", (req, res) => {
  const server = store.getMcpServer(req.params.tenant_id, req.params.server_id);

  if (!server) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "MCP server not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatMcpServer(server));
});

// PUT /api/tenants/:tenant_id/mcp-servers/:server_id - Update MCP server
router.put("/:server_id", (req, res) => {
  const {
    display_name,
    url,
    command,
    args,
    env,
    headers_template,
    allowed_tools,
    tools,
    description,
    openapi_spec,
    openapi_base_url,
    status,
  } = req.body;

  const server = store.updateMcpServer(req.params.tenant_id, req.params.server_id, {
    displayName: display_name,
    url,
    command,
    args,
    env,
    headersTemplate: headers_template,
    allowedTools: allowed_tools,
    tools,
    description,
    openapiSpec: openapi_spec,
    openapiBaseUrl: openapi_base_url,
    status,
  });

  if (!server) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "MCP server not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatMcpServer(server));
});

// DELETE /api/tenants/:tenant_id/mcp-servers/:server_id - Delete MCP server
router.delete("/:server_id", (req, res) => {
  const deleted = store.deleteMcpServer(req.params.tenant_id, req.params.server_id);

  if (!deleted) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "MCP server not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.status(204).send();
});

module.exports = router;
