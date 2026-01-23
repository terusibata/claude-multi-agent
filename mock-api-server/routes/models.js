const express = require("express");
const store = require("../store/memory-store");

const router = express.Router();

// Helper to format model response
function formatModel(m) {
  return {
    model_id: m.modelId,
    display_name: m.displayName,
    bedrock_model_id: m.bedrockModelId,
    model_region: m.modelRegion,
    input_token_price: m.inputTokenPrice,
    output_token_price: m.outputTokenPrice,
    cache_creation_5m_price: m.cacheCreation5mPrice,
    cache_creation_1h_price: m.cacheCreation1hPrice,
    cache_read_price: m.cacheReadPrice,
    status: m.status,
    created_at: m.createdAt,
    updated_at: m.updatedAt,
  };
}

// GET /api/models - List models
router.get("/", (req, res) => {
  const { status } = req.query;
  const models = store.getAllModels(status);
  res.json(models.map(formatModel));
});

// POST /api/models - Create model
router.post("/", (req, res) => {
  const {
    model_id,
    display_name,
    bedrock_model_id,
    model_region,
    input_token_price,
    output_token_price,
    cache_creation_5m_price,
    cache_creation_1h_price,
    cache_read_price,
  } = req.body;

  if (!model_id || !display_name || !bedrock_model_id || !input_token_price || !output_token_price) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "Required fields missing",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  if (store.getModel(model_id)) {
    return res.status(409).json({
      error: {
        code: "CONFLICT",
        message: "Model already exists",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const model = store.createModel({
    modelId: model_id,
    displayName: display_name,
    bedrockModelId: bedrock_model_id,
    modelRegion: model_region,
    inputTokenPrice: input_token_price,
    outputTokenPrice: output_token_price,
    cacheCreation5mPrice: cache_creation_5m_price,
    cacheCreation1hPrice: cache_creation_1h_price,
    cacheReadPrice: cache_read_price,
  });

  res.status(201).json(formatModel(model));
});

// GET /api/models/:model_id - Get model
router.get("/:model_id", (req, res) => {
  const model = store.getModel(req.params.model_id);

  if (!model) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Model not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatModel(model));
});

// PUT /api/models/:model_id - Update model
router.put("/:model_id", (req, res) => {
  const {
    display_name,
    bedrock_model_id,
    model_region,
    input_token_price,
    output_token_price,
    cache_creation_5m_price,
    cache_creation_1h_price,
    cache_read_price,
  } = req.body;

  const model = store.updateModel(req.params.model_id, {
    displayName: display_name,
    bedrockModelId: bedrock_model_id,
    modelRegion: model_region,
    inputTokenPrice: input_token_price,
    outputTokenPrice: output_token_price,
    cacheCreation5mPrice: cache_creation_5m_price,
    cacheCreation1hPrice: cache_creation_1h_price,
    cacheReadPrice: cache_read_price,
  });

  if (!model) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Model not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatModel(model));
});

// PATCH /api/models/:model_id/status - Update model status
router.patch("/:model_id/status", (req, res) => {
  const { status } = req.query;

  if (!["active", "deprecated"].includes(status)) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "Invalid status. Must be 'active' or 'deprecated'",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const model = store.updateModel(req.params.model_id, { status });

  if (!model) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Model not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatModel(model));
});

// DELETE /api/models/:model_id - Delete model
router.delete("/:model_id", (req, res) => {
  const deleted = store.deleteModel(req.params.model_id);

  if (!deleted) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Model not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.status(204).send();
});

module.exports = router;
