const express = require("express");
const { randomFloat } = require("../utils/random");

const router = express.Router();

// GET /health - Detailed health check
router.get("/", (req, res) => {
  res.json({
    status: "healthy",
    version: "1.0.0-mock",
    environment: "mock",
    timestamp: new Date().toISOString(),
    checks: {
      database: { status: "healthy", latencyMs: randomFloat(1, 10) },
      redis: { status: "healthy", latencyMs: randomFloat(0.5, 5) },
      s3: { status: "healthy", latencyMs: randomFloat(10, 50) },
    },
  });
});

// GET /health/live - Liveness probe
router.get("/live", (req, res) => {
  res.json({ status: "alive" });
});

// GET /health/ready - Readiness probe
router.get("/ready", (req, res) => {
  res.json({ status: "ready" });
});

module.exports = router;
