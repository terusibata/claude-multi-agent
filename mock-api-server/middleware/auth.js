// Mock authentication middleware
// Accepts any API key for testing purposes

function authMiddleware(req, res, next) {
  const apiKey = req.headers["x-api-key"] || extractBearerToken(req.headers["authorization"]);

  if (!apiKey) {
    return res.status(401).json({
      error: {
        code: "UNAUTHORIZED",
        message: "API key is required",
        details: [],
        requestId: req.requestId,
        timestamp: new Date().toISOString(),
      },
    });
  }

  // Mock: Accept any API key
  req.apiKey = apiKey;
  next();
}

function extractBearerToken(authHeader) {
  if (!authHeader) return null;
  const parts = authHeader.split(" ");
  if (parts.length === 2 && parts[0].toLowerCase() === "bearer") {
    return parts[1];
  }
  return null;
}

// Optional auth - doesn't require API key but extracts if present
function optionalAuth(req, res, next) {
  const apiKey = req.headers["x-api-key"] || extractBearerToken(req.headers["authorization"]);
  req.apiKey = apiKey || null;
  next();
}

module.exports = { authMiddleware, optionalAuth };
