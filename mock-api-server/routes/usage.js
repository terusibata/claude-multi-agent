const express = require("express");
const store = require("../store/memory-store");
const { randomInt, randomFloat } = require("../utils/random");

const router = express.Router({ mergeParams: true });

// Helper to format usage log response
function formatUsageLog(l) {
  return {
    usage_log_id: l.usageLogId,
    tenant_id: l.tenantId,
    user_id: l.userId,
    model_id: l.modelId,
    session_id: l.sessionId,
    conversation_id: l.conversationId,
    input_tokens: l.inputTokens,
    output_tokens: l.outputTokens,
    cache_creation_5m_tokens: l.cacheCreation5mTokens,
    cache_creation_1h_tokens: l.cacheCreation1hTokens,
    cache_read_tokens: l.cacheReadTokens,
    total_tokens: l.totalTokens,
    cost_usd: l.costUsd,
    executed_at: l.executedAt,
  };
}

// Helper to format tool log response
function formatToolLog(l) {
  return {
    tool_log_id: l.toolLogId,
    session_id: l.sessionId,
    conversation_id: l.conversationId,
    tool_name: l.toolName,
    tool_use_id: l.toolUseId,
    tool_input: l.toolInput,
    tool_output: l.toolOutput,
    status: l.status,
    execution_time_ms: l.executionTimeMs,
    executed_at: l.executedAt,
  };
}

// GET /api/tenants/:tenant_id/usage - Get usage logs
router.get("/usage", (req, res) => {
  const { user_id, from_date, to_date, limit = 100, offset = 0 } = req.query;

  let logs = store.getUsageLogs(req.params.tenant_id, {
    userId: user_id,
    fromDate: from_date,
    toDate: to_date,
  });

  logs = logs.slice(parseInt(offset), parseInt(offset) + parseInt(limit));
  res.json(logs.map(formatUsageLog));
});

// GET /api/tenants/:tenant_id/usage/users/:user_id - Get user usage logs
router.get("/usage/users/:user_id", (req, res) => {
  const { from_date, to_date, limit = 100, offset = 0 } = req.query;

  let logs = store.getUsageLogs(req.params.tenant_id, {
    userId: req.params.user_id,
    fromDate: from_date,
    toDate: to_date,
  });

  logs = logs.slice(parseInt(offset), parseInt(offset) + parseInt(limit));
  res.json(logs.map(formatUsageLog));
});

// GET /api/tenants/:tenant_id/usage/summary - Get usage summary
router.get("/usage/summary", (req, res) => {
  const { from_date, to_date, group_by = "day" } = req.query;
  const tenantId = req.params.tenant_id;

  const logs = store.getUsageLogs(tenantId, {
    fromDate: from_date,
    toDate: to_date,
  });

  // Calculate totals
  let totalInputTokens = 0;
  let totalOutputTokens = 0;
  let totalCost = 0;

  for (const log of logs) {
    totalInputTokens += log.inputTokens;
    totalOutputTokens += log.outputTokens;
    totalCost += parseFloat(log.costUsd);
  }

  // Generate mock grouped data
  const groupedData = [];
  const now = new Date();
  const periods = group_by === "day" ? 7 : group_by === "week" ? 4 : 3;

  for (let i = 0; i < periods; i++) {
    const date = new Date(now);
    if (group_by === "day") {
      date.setDate(date.getDate() - i);
    } else if (group_by === "week") {
      date.setDate(date.getDate() - i * 7);
    } else {
      date.setMonth(date.getMonth() - i);
    }

    groupedData.push({
      period: date.toISOString().split("T")[0],
      input_tokens: randomInt(1000, 10000),
      output_tokens: randomInt(500, 5000),
      total_tokens: randomInt(1500, 15000),
      cost_usd: randomFloat(0.01, 1.0).toString(),
      execution_count: randomInt(5, 50),
    });
  }

  res.json({
    tenant_id: tenantId,
    from_date: from_date || new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString(),
    to_date: to_date || now.toISOString(),
    group_by,
    summary: {
      total_input_tokens: totalInputTokens || randomInt(10000, 100000),
      total_output_tokens: totalOutputTokens || randomInt(5000, 50000),
      total_cost_usd: totalCost.toFixed(4) || randomFloat(1, 50).toString(),
      total_executions: logs.length || randomInt(50, 500),
    },
    data: groupedData,
  });
});

// GET /api/tenants/:tenant_id/cost-report - Get cost report
router.get("/cost-report", (req, res) => {
  const { from_date, to_date, model_id, user_id } = req.query;
  const tenantId = req.params.tenant_id;

  let logs = store.getUsageLogs(tenantId, {
    fromDate: from_date,
    toDate: to_date,
    userId: user_id,
  });

  if (model_id) {
    logs = logs.filter((l) => l.modelId === model_id);
  }

  // Aggregate by model
  const byModel = new Map();
  let totalCost = 0;
  let totalTokens = 0;

  for (const log of logs) {
    totalCost += parseFloat(log.costUsd);
    totalTokens += log.totalTokens;

    if (!byModel.has(log.modelId)) {
      byModel.set(log.modelId, {
        model_id: log.modelId,
        model_name: store.getModel(log.modelId)?.displayName || log.modelId,
        total_tokens: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_creation_5m_tokens: 0,
        cache_creation_1h_tokens: 0,
        cache_read_tokens: 0,
        cost_usd: 0,
        execution_count: 0,
      });
    }

    const modelStats = byModel.get(log.modelId);
    modelStats.total_tokens += log.totalTokens;
    modelStats.input_tokens += log.inputTokens;
    modelStats.output_tokens += log.outputTokens;
    modelStats.cache_creation_5m_tokens += log.cacheCreation5mTokens;
    modelStats.cache_creation_1h_tokens += log.cacheCreation1hTokens;
    modelStats.cache_read_tokens += log.cacheReadTokens;
    modelStats.cost_usd += parseFloat(log.costUsd);
    modelStats.execution_count++;
  }

  // Format cost_usd as string
  for (const stats of byModel.values()) {
    stats.cost_usd = stats.cost_usd.toFixed(6);
  }

  // Generate mock data if no logs
  if (byModel.size === 0) {
    const models = store.getAllModels("active");
    for (const model of models) {
      byModel.set(model.modelId, {
        model_id: model.modelId,
        model_name: model.displayName,
        total_tokens: randomInt(10000, 100000),
        input_tokens: randomInt(5000, 50000),
        output_tokens: randomInt(5000, 50000),
        cache_creation_5m_tokens: randomInt(0, 5000),
        cache_creation_1h_tokens: randomInt(0, 3000),
        cache_read_tokens: randomInt(0, 10000),
        cost_usd: randomFloat(0.5, 10).toFixed(6),
        execution_count: randomInt(10, 100),
      });
      totalCost += parseFloat(byModel.get(model.modelId).cost_usd);
      totalTokens += byModel.get(model.modelId).total_tokens;
    }
  }

  const now = new Date();

  res.json({
    tenant_id: tenantId,
    from_date: from_date || new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString(),
    to_date: to_date || now.toISOString(),
    total_cost_usd: totalCost.toFixed(6),
    total_tokens: totalTokens,
    total_executions: logs.length || randomInt(50, 500),
    by_model: Array.from(byModel.values()),
  });
});

// GET /api/tenants/:tenant_id/tool-logs - Get tool logs
router.get("/tool-logs", (req, res) => {
  const { session_id, tool_name, from_date, to_date, limit = 100, offset = 0 } = req.query;

  let logs = store.getToolLogs(req.params.tenant_id, {
    sessionId: session_id,
    toolName: tool_name,
    fromDate: from_date,
    toDate: to_date,
  });

  logs = logs.slice(parseInt(offset), parseInt(offset) + parseInt(limit));
  res.json(logs.map(formatToolLog));
});

module.exports = router;
