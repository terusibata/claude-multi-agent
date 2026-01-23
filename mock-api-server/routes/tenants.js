const express = require("express");
const store = require("../store/memory-store");

const router = express.Router();

// GET /api/tenants - List tenants
router.get("/", (req, res) => {
  const { status, limit = 100, offset = 0 } = req.query;
  let tenants = store.getAllTenants(status);
  tenants = tenants.slice(parseInt(offset), parseInt(offset) + parseInt(limit));

  res.json(
    tenants.map((t) => ({
      tenant_id: t.tenantId,
      system_prompt: t.systemPrompt,
      model_id: t.modelId,
      status: t.status,
      created_at: t.createdAt,
      updated_at: t.updatedAt,
    }))
  );
});

// POST /api/tenants - Create tenant
router.post("/", (req, res) => {
  const { tenant_id, system_prompt, model_id } = req.body;

  if (!tenant_id) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "tenant_id is required",
        details: [{ field: "tenant_id", message: "This field is required" }],
        timestamp: new Date().toISOString(),
      },
    });
  }

  if (store.getTenant(tenant_id)) {
    return res.status(409).json({
      error: {
        code: "CONFLICT",
        message: "Tenant already exists",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const tenant = store.createTenant({
    tenantId: tenant_id,
    systemPrompt: system_prompt,
    modelId: model_id,
  });

  res.status(201).json({
    tenant_id: tenant.tenantId,
    system_prompt: tenant.systemPrompt,
    model_id: tenant.modelId,
    status: tenant.status,
    created_at: tenant.createdAt,
    updated_at: tenant.updatedAt,
  });
});

// GET /api/tenants/:tenant_id - Get tenant
router.get("/:tenant_id", (req, res) => {
  const tenant = store.getTenant(req.params.tenant_id);

  if (!tenant) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Tenant not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json({
    tenant_id: tenant.tenantId,
    system_prompt: tenant.systemPrompt,
    model_id: tenant.modelId,
    status: tenant.status,
    created_at: tenant.createdAt,
    updated_at: tenant.updatedAt,
  });
});

// PUT /api/tenants/:tenant_id - Update tenant
router.put("/:tenant_id", (req, res) => {
  const { system_prompt, model_id, status } = req.body;

  const tenant = store.updateTenant(req.params.tenant_id, {
    systemPrompt: system_prompt,
    modelId: model_id,
    status: status,
  });

  if (!tenant) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Tenant not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json({
    tenant_id: tenant.tenantId,
    system_prompt: tenant.systemPrompt,
    model_id: tenant.modelId,
    status: tenant.status,
    created_at: tenant.createdAt,
    updated_at: tenant.updatedAt,
  });
});

// DELETE /api/tenants/:tenant_id - Delete tenant
router.delete("/:tenant_id", (req, res) => {
  const deleted = store.deleteTenant(req.params.tenant_id);

  if (!deleted) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Tenant not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.status(204).send();
});

module.exports = router;
