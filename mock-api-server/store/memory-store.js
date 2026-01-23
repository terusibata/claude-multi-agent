const { v4: uuidv4 } = require("uuid");

const now = () => new Date().toISOString();

// Initial models data
const initialModels = [
  {
    modelId: "global.anthropic.claude-sonnet4-5-20250929-v1:0",
    displayName: "Anthropic Claude Sonnet-4.5",
    bedrockModelId: "global.anthropic.claude-sonnet4-5-20250929-v1:0",
    modelRegion: "us-east-1",
    inputTokenPrice: "0.003",
    outputTokenPrice: "0.015",
    cacheCreation5mPrice: "0.00375",
    cacheCreation1hPrice: "0.006",
    cacheReadPrice: "0.0003",
    status: "active",
    createdAt: now(),
    updatedAt: now(),
  },
];

// Initial tenant for testing
const initialTenants = [
  {
    tenantId: "default-tenant",
    systemPrompt: "You are a helpful AI assistant.",
    modelId: "global.anthropic.claude-sonnet4-5-20250929-v1:0",
    status: "active",
    createdAt: now(),
    updatedAt: now(),
  },
];

class MemoryStore {
  constructor() {
    this.models = new Map();
    this.tenants = new Map();
    this.skills = new Map(); // key: `${tenantId}:${skillId}`
    this.mcpServers = new Map(); // key: `${tenantId}:${serverId}`
    this.conversations = new Map(); // key: `${tenantId}:${conversationId}`
    this.messages = new Map(); // key: conversationId, value: array of messages
    this.usageLogs = []; // array of usage logs
    this.toolLogs = []; // array of tool logs
    this.workspaceFiles = new Map(); // key: conversationId, value: array of files
    this.simpleChats = new Map(); // key: `${tenantId}:${chatId}`
    this.simpleChatMessages = new Map(); // key: chatId, value: array of messages

    this._initializeData();
  }

  _initializeData() {
    // Initialize models
    for (const model of initialModels) {
      this.models.set(model.modelId, model);
    }

    // Initialize tenants
    for (const tenant of initialTenants) {
      this.tenants.set(tenant.tenantId, tenant);
    }
  }

  // Models
  getAllModels(status) {
    const models = Array.from(this.models.values());
    if (status) {
      return models.filter((m) => m.status === status);
    }
    return models;
  }

  getModel(modelId) {
    return this.models.get(modelId);
  }

  createModel(data) {
    const model = {
      modelId: data.modelId,
      displayName: data.displayName,
      bedrockModelId: data.bedrockModelId,
      modelRegion: data.modelRegion || "us-east-1",
      inputTokenPrice: data.inputTokenPrice,
      outputTokenPrice: data.outputTokenPrice,
      cacheCreation5mPrice: data.cacheCreation5mPrice || "0",
      cacheCreation1hPrice: data.cacheCreation1hPrice || "0",
      cacheReadPrice: data.cacheReadPrice || "0",
      status: "active",
      createdAt: now(),
      updatedAt: now(),
    };
    this.models.set(model.modelId, model);
    return model;
  }

  updateModel(modelId, data) {
    const model = this.models.get(modelId);
    if (!model) return null;
    Object.assign(model, data, { updatedAt: now() });
    return model;
  }

  deleteModel(modelId) {
    return this.models.delete(modelId);
  }

  // Tenants
  getAllTenants(status) {
    const tenants = Array.from(this.tenants.values());
    if (status) {
      return tenants.filter((t) => t.status === status);
    }
    return tenants;
  }

  getTenant(tenantId) {
    return this.tenants.get(tenantId);
  }

  createTenant(data) {
    const tenant = {
      tenantId: data.tenantId,
      systemPrompt: data.systemPrompt || "",
      modelId: data.modelId || initialModels[0].modelId,
      status: "active",
      createdAt: now(),
      updatedAt: now(),
    };
    this.tenants.set(tenant.tenantId, tenant);
    return tenant;
  }

  updateTenant(tenantId, data) {
    const tenant = this.tenants.get(tenantId);
    if (!tenant) return null;
    Object.assign(tenant, data, { updatedAt: now() });
    return tenant;
  }

  deleteTenant(tenantId) {
    return this.tenants.delete(tenantId);
  }

  // Skills
  getSkillsForTenant(tenantId, status) {
    const skills = [];
    for (const [key, skill] of this.skills) {
      if (key.startsWith(`${tenantId}:`)) {
        if (!status || skill.status === status) {
          skills.push(skill);
        }
      }
    }
    return skills;
  }

  getSkill(tenantId, skillId) {
    return this.skills.get(`${tenantId}:${skillId}`);
  }

  createSkill(tenantId, data) {
    const skillId = uuidv4();
    const skill = {
      skillId,
      tenantId,
      name: data.name,
      displayTitle: data.displayTitle || data.name,
      description: data.description || "",
      slashCommand: `/${data.name.toLowerCase().replace(/\s+/g, "-")}`,
      slashCommandDescription: data.description || "",
      isUserSelectable: true,
      version: 1,
      filePath: `skills/${tenantId}/${skillId}/skill.md`,
      status: "active",
      createdAt: now(),
      updatedAt: now(),
    };
    this.skills.set(`${tenantId}:${skillId}`, skill);
    return skill;
  }

  updateSkill(tenantId, skillId, data) {
    const skill = this.skills.get(`${tenantId}:${skillId}`);
    if (!skill) return null;
    Object.assign(skill, data, { updatedAt: now(), version: skill.version + 1 });
    return skill;
  }

  deleteSkill(tenantId, skillId) {
    return this.skills.delete(`${tenantId}:${skillId}`);
  }

  // MCP Servers
  getMcpServersForTenant(tenantId, status) {
    const servers = [];
    for (const [key, server] of this.mcpServers) {
      if (key.startsWith(`${tenantId}:`)) {
        if (!status || server.status === status) {
          servers.push(server);
        }
      }
    }
    return servers;
  }

  getMcpServer(tenantId, serverId) {
    return this.mcpServers.get(`${tenantId}:${serverId}`);
  }

  createMcpServer(tenantId, data) {
    const serverId = uuidv4();
    const server = {
      mcpServerId: serverId,
      tenantId,
      name: data.name,
      displayName: data.displayName || data.name,
      type: data.type,
      url: data.url || null,
      command: data.command || null,
      args: data.args || [],
      env: data.env || {},
      headersTemplate: data.headersTemplate || {},
      allowedTools: data.allowedTools || [],
      tools: data.tools || [],
      description: data.description || "",
      openapiSpec: data.openapiSpec || null,
      openapiBaseUrl: data.openapiBaseUrl || null,
      status: "active",
      createdAt: now(),
      updatedAt: now(),
    };
    this.mcpServers.set(`${tenantId}:${serverId}`, server);
    return server;
  }

  updateMcpServer(tenantId, serverId, data) {
    const server = this.mcpServers.get(`${tenantId}:${serverId}`);
    if (!server) return null;
    Object.assign(server, data, { updatedAt: now() });
    return server;
  }

  deleteMcpServer(tenantId, serverId) {
    return this.mcpServers.delete(`${tenantId}:${serverId}`);
  }

  // Conversations
  getConversationsForTenant(tenantId, filters = {}) {
    const conversations = [];
    for (const [key, conv] of this.conversations) {
      if (key.startsWith(`${tenantId}:`)) {
        let match = true;
        if (filters.userId && conv.userId !== filters.userId) match = false;
        if (filters.status && conv.status !== filters.status) match = false;
        if (filters.fromDate && new Date(conv.createdAt) < new Date(filters.fromDate)) match = false;
        if (filters.toDate && new Date(conv.createdAt) > new Date(filters.toDate)) match = false;
        if (match) conversations.push(conv);
      }
    }
    return conversations;
  }

  getConversation(tenantId, conversationId) {
    return this.conversations.get(`${tenantId}:${conversationId}`);
  }

  createConversation(tenantId, data) {
    const conversationId = uuidv4();
    const sessionId = uuidv4();
    const conversation = {
      conversationId,
      sessionId,
      tenantId,
      userId: data.userId,
      modelId: data.modelId || this.tenants.get(tenantId)?.modelId || initialModels[0].modelId,
      title: "New Conversation",
      status: "active",
      workspaceEnabled: data.workspaceEnabled !== false,
      createdAt: now(),
      updatedAt: now(),
    };
    this.conversations.set(`${tenantId}:${conversationId}`, conversation);
    this.messages.set(conversationId, []);
    this.workspaceFiles.set(conversationId, []);
    return conversation;
  }

  updateConversation(tenantId, conversationId, data) {
    const conv = this.conversations.get(`${tenantId}:${conversationId}`);
    if (!conv) return null;
    Object.assign(conv, data, { updatedAt: now() });
    return conv;
  }

  deleteConversation(tenantId, conversationId) {
    this.messages.delete(conversationId);
    this.workspaceFiles.delete(conversationId);
    return this.conversations.delete(`${tenantId}:${conversationId}`);
  }

  // Messages
  getMessagesForConversation(conversationId) {
    return this.messages.get(conversationId) || [];
  }

  addMessage(conversationId, message) {
    const messages = this.messages.get(conversationId) || [];
    const seq = messages.length + 1;
    const msg = {
      messageId: uuidv4(),
      conversationId,
      messageSeq: seq,
      messageType: message.messageType,
      messageSubtype: message.messageSubtype || null,
      content: message.content || null,
      timestamp: now(),
    };
    messages.push(msg);
    this.messages.set(conversationId, messages);
    return msg;
  }

  // Usage Logs
  addUsageLog(data) {
    const log = {
      usageLogId: uuidv4(),
      tenantId: data.tenantId,
      userId: data.userId,
      modelId: data.modelId,
      sessionId: data.sessionId || null,
      conversationId: data.conversationId || null,
      inputTokens: data.inputTokens || 0,
      outputTokens: data.outputTokens || 0,
      cacheCreation5mTokens: data.cacheCreation5mTokens || 0,
      cacheCreation1hTokens: data.cacheCreation1hTokens || 0,
      cacheReadTokens: data.cacheReadTokens || 0,
      totalTokens: data.totalTokens || 0,
      costUsd: data.costUsd || "0",
      executedAt: now(),
    };
    this.usageLogs.push(log);
    return log;
  }

  getUsageLogs(tenantId, filters = {}) {
    return this.usageLogs.filter((log) => {
      if (log.tenantId !== tenantId) return false;
      if (filters.userId && log.userId !== filters.userId) return false;
      if (filters.fromDate && new Date(log.executedAt) < new Date(filters.fromDate)) return false;
      if (filters.toDate && new Date(log.executedAt) > new Date(filters.toDate)) return false;
      return true;
    });
  }

  // Tool Logs
  addToolLog(data) {
    const log = {
      toolLogId: uuidv4(),
      sessionId: data.sessionId,
      conversationId: data.conversationId || null,
      toolName: data.toolName,
      toolUseId: data.toolUseId || null,
      toolInput: data.toolInput || null,
      toolOutput: data.toolOutput || null,
      status: data.status || "success",
      executionTimeMs: data.executionTimeMs || null,
      executedAt: now(),
    };
    this.toolLogs.push(log);
    return log;
  }

  getToolLogs(tenantId, filters = {}) {
    return this.toolLogs.filter((log) => {
      if (filters.sessionId && log.sessionId !== filters.sessionId) return false;
      if (filters.toolName && log.toolName !== filters.toolName) return false;
      if (filters.fromDate && new Date(log.executedAt) < new Date(filters.fromDate)) return false;
      if (filters.toDate && new Date(log.executedAt) > new Date(filters.toDate)) return false;
      return true;
    });
  }

  // Workspace Files
  getWorkspaceFiles(conversationId) {
    return this.workspaceFiles.get(conversationId) || [];
  }

  addWorkspaceFile(conversationId, file) {
    const files = this.workspaceFiles.get(conversationId) || [];
    const f = {
      fileId: uuidv4(),
      filePath: file.filePath,
      originalName: file.originalName,
      fileSize: file.fileSize || 0,
      mimeType: file.mimeType || null,
      version: 1,
      source: file.source || "user_upload",
      isPresented: file.isPresented || false,
      checksum: file.checksum || null,
      description: file.description || null,
      createdAt: now(),
      updatedAt: now(),
    };
    files.push(f);
    this.workspaceFiles.set(conversationId, files);
    return f;
  }

  // Simple Chats
  getSimpleChatsForTenant(tenantId, filters = {}) {
    const chats = [];
    for (const [key, chat] of this.simpleChats) {
      if (key.startsWith(`${tenantId}:`)) {
        let match = true;
        if (filters.userId && chat.userId !== filters.userId) match = false;
        if (filters.applicationType && chat.applicationType !== filters.applicationType) match = false;
        if (filters.status && chat.status !== filters.status) match = false;
        if (match) chats.push(chat);
      }
    }
    return chats;
  }

  getSimpleChat(tenantId, chatId) {
    return this.simpleChats.get(`${tenantId}:${chatId}`);
  }

  createSimpleChat(tenantId, data) {
    const chatId = uuidv4();
    const chat = {
      chatId,
      tenantId,
      userId: data.userId,
      modelId: data.modelId || initialModels[0].modelId,
      applicationType: data.applicationType,
      systemPrompt: data.systemPrompt || "",
      title: null,
      status: "active",
      createdAt: now(),
      updatedAt: now(),
    };
    this.simpleChats.set(`${tenantId}:${chatId}`, chat);
    this.simpleChatMessages.set(chatId, []);
    return chat;
  }

  updateSimpleChat(tenantId, chatId, data) {
    const chat = this.simpleChats.get(`${tenantId}:${chatId}`);
    if (!chat) return null;
    Object.assign(chat, data, { updatedAt: now() });
    return chat;
  }

  deleteSimpleChat(tenantId, chatId) {
    this.simpleChatMessages.delete(chatId);
    return this.simpleChats.delete(`${tenantId}:${chatId}`);
  }

  getSimpleChatMessages(chatId) {
    return this.simpleChatMessages.get(chatId) || [];
  }

  addSimpleChatMessage(chatId, role, content) {
    const messages = this.simpleChatMessages.get(chatId) || [];
    const seq = messages.length + 1;
    const msg = {
      messageId: uuidv4(),
      chatId,
      messageSeq: seq,
      role,
      content,
      createdAt: now(),
    };
    messages.push(msg);
    this.simpleChatMessages.set(chatId, messages);
    return msg;
  }
}

// Singleton instance
const store = new MemoryStore();

module.exports = store;
