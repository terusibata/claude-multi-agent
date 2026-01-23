const express = require("express");
const multer = require("multer");
const store = require("../store/memory-store");

const router = express.Router({ mergeParams: true });
const upload = multer({ storage: multer.memoryStorage() });

// Helper to format skill response
function formatSkill(s) {
  return {
    skill_id: s.skillId,
    tenant_id: s.tenantId,
    name: s.name,
    display_title: s.displayTitle,
    description: s.description,
    slash_command: s.slashCommand,
    slash_command_description: s.slashCommandDescription,
    is_user_selectable: s.isUserSelectable,
    version: s.version,
    file_path: s.filePath,
    status: s.status,
    created_at: s.createdAt,
    updated_at: s.updatedAt,
  };
}

// GET /api/tenants/:tenant_id/skills - List skills
router.get("/", (req, res) => {
  const { status } = req.query;
  const skills = store.getSkillsForTenant(req.params.tenant_id, status);
  res.json(skills.map(formatSkill));
});

// POST /api/tenants/:tenant_id/skills - Upload skill
router.post("/", upload.fields([{ name: "skill_md" }, { name: "additional_files" }]), (req, res) => {
  const { name, display_title, description } = req.body;
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

  if (!name) {
    return res.status(422).json({
      error: {
        code: "VALIDATION_ERROR",
        message: "name is required",
        details: [{ field: "name", message: "This field is required" }],
        timestamp: new Date().toISOString(),
      },
    });
  }

  const skill = store.createSkill(tenantId, {
    name,
    displayTitle: display_title,
    description,
  });

  res.status(201).json(formatSkill(skill));
});

// GET /api/tenants/:tenant_id/skills/slash-commands - List slash commands
router.get("/slash-commands", (req, res) => {
  const skills = store.getSkillsForTenant(req.params.tenant_id, "active");

  res.json({
    items: skills.map((s) => ({
      skill_id: s.skillId,
      name: s.name,
      slash_command: s.slashCommand,
      description: s.slashCommandDescription,
    })),
  });
});

// GET /api/tenants/:tenant_id/skills/:skill_id - Get skill
router.get("/:skill_id", (req, res) => {
  const skill = store.getSkill(req.params.tenant_id, req.params.skill_id);

  if (!skill) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Skill not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatSkill(skill));
});

// PUT /api/tenants/:tenant_id/skills/:skill_id - Update skill metadata
router.put("/:skill_id", (req, res) => {
  const { display_title, description, is_user_selectable, status } = req.body;

  const skill = store.updateSkill(req.params.tenant_id, req.params.skill_id, {
    displayTitle: display_title,
    description,
    isUserSelectable: is_user_selectable,
    status,
  });

  if (!skill) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Skill not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json(formatSkill(skill));
});

// PUT /api/tenants/:tenant_id/skills/:skill_id/files - Update skill files
router.put("/:skill_id/files", upload.array("files"), (req, res) => {
  const skill = store.getSkill(req.params.tenant_id, req.params.skill_id);

  if (!skill) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Skill not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  // Just increment version for mock
  store.updateSkill(req.params.tenant_id, req.params.skill_id, {});
  res.json(formatSkill(store.getSkill(req.params.tenant_id, req.params.skill_id)));
});

// GET /api/tenants/:tenant_id/skills/:skill_id/files - List skill files
router.get("/:skill_id/files", (req, res) => {
  const skill = store.getSkill(req.params.tenant_id, req.params.skill_id);

  if (!skill) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Skill not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json({
    skill_id: skill.skillId,
    files: [
      {
        file_path: "skill.md",
        file_size: 1024,
        updated_at: skill.updatedAt,
      },
    ],
  });
});

// GET /api/tenants/:tenant_id/skills/:skill_id/files/:file_path - Get skill file content
router.get("/:skill_id/files/*", (req, res) => {
  const skill = store.getSkill(req.params.tenant_id, req.params.skill_id);

  if (!skill) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Skill not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.json({
    content: `# ${skill.name}\n\n${skill.description}\n\nThis is mock skill content.`,
  });
});

// DELETE /api/tenants/:tenant_id/skills/:skill_id - Delete skill
router.delete("/:skill_id", (req, res) => {
  const deleted = store.deleteSkill(req.params.tenant_id, req.params.skill_id);

  if (!deleted) {
    return res.status(404).json({
      error: {
        code: "NOT_FOUND",
        message: "Skill not found",
        details: [],
        timestamp: new Date().toISOString(),
      },
    });
  }

  res.status(204).send();
});

module.exports = router;
