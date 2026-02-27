# コンテナ起動時の Skills・MCP サーバー設定

AgentCore Runtime コンテナ（workspace-agent）内で、スキルと MCP サーバーが自動的に利用可能になる仕組みをまとめる。

## Skills 同期

| 項目 | 内容 |
|------|------|
| ホスト保存先 | `{SKILLS_BASE_PATH}/tenant_{tenant_id}/.claude/skills/{skill_name}/` |
| コンテナ同期先 | `/workspace/.claude/skills/{skill_name}/` |
| 同期方式 | base64 エンコードして AgentCore ペイロードに含めて送信 |
| SDK 設定 | `cwd=/workspace`, `setting_sources=["project"]`（スキル同期成功時のみ） |
| 実装箇所 | `app/services/execute_service.py:_build_skill_files_payload()` |

スキル同期は AgentCore `invoke_agent_runtime` のペイロードに `skill_files` として base64 エンコードされたファイルを含め、コンテナ内の `index.ts:writeSkillFiles()` でディスクに書き出す方式。

## ビルトイン MCP サーバー

| サーバー名 | ツール数 | 用途 |
|-----------|---------|------|
| `file-presentation` | 1 | AI 作成ファイルの提示 (`present_files`) |
| `file-tools` | 15 | Excel/PDF/Word/PowerPoint/画像の読み書き |

実装: `workspace-agent/src/builtin-mcp.ts` → `workspace-agent/src/sdk-client.ts:buildSdkOptions()` で SDK に登録。

## OpenAPI MCP サーバー（動的）

テナントの DB に登録された `mcp_servers`（status=active）からリクエスト時に動的生成。

| フロー | 実装箇所 |
|--------|---------|
| DB 取得 → 設定シリアライズ | `app/services/mcp_config_builder.py:build_mcp_server_configs()` |
| コンテナ内生成 | `workspace-agent/src/builtin-mcp.ts:createOpenApiMcpServers()` |
| OpenAPI → MCP ツール変換 | `workspace-agent/src/openapi-mcp.ts:createOpenApiMcpServer()` |

## ストリーミング入力モード

TypeScript SDK (`@anthropic-ai/claude-agent-sdk`) の `query()` 関数は `prompt` に文字列を直接渡すだけで MCP サーバーのツールを自動的に使用できる。Python SDK で必要だったストリーミング入力モードは不要。

```typescript
// workspace-agent/src/sdk-client.ts
for await (const message of query({ prompt: request.user_input, options })) {
  // MCP ツールも含めて自動的に呼び出される
}
```

## allowed_tools

`app/services/mcp_config_builder.py:compute_allowed_tools()` で計算し、ペイロードでコンテナに渡す。

- `mcp__file-tools__*`
- `mcp__file-presentation__*`
- `mcp__{server_name}__*`（OpenAPI MCP サーバーごと）

## Init イベントでの確認

コンテナから返される `init` イベントの `tools` 配列に `mcp__<server>__<tool>` 形式で全ツールが含まれていれば正常。
