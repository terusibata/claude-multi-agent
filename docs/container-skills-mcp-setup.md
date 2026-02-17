# コンテナ起動時の Skills・MCP サーバー設定

ワークスペースコンテナ新規作成時に、スキルと MCP サーバーが自動的に利用可能になる仕組みをまとめる。

## Skills 同期

| 項目 | 内容 |
|------|------|
| ホスト保存先 | `{SKILLS_BASE_PATH}/tenant_{tenant_id}/.claude/skills/{skill_name}/` |
| コンテナ同期先 | `/workspace/.claude/skills/{skill_name}/` |
| SDK 設定 | `cwd=/workspace`, `setting_sources=["project"]`（スキル同期成功時のみ） |
| 実装箇所 | `execute_service.py:_sync_skills_to_container()` |

スキル同期は `exec` 経由でコンテナ内に直接書き込むため、S3 設定の有無に依存しない。

## ビルトイン MCP サーバー

| サーバー名 | ツール数 | 用途 |
|-----------|---------|------|
| `file-presentation` | 1 | AI 作成ファイルの提示 (`present_files`) |
| `file-tools` | 15 | Excel/PDF/Word/PowerPoint/画像の読み書き |

実装: `workspace_agent/builtin_mcp.py` → `sdk_client.py:_build_sdk_options()` で SDK に登録。

## OpenAPI MCP サーバー（動的）

テナントの DB に登録された `mcp_servers`（status=active）からリクエスト時に動的生成。

| フロー | 実装箇所 |
|--------|---------|
| DB 取得 → 設定シリアライズ | `execute_service.py:_build_mcp_server_configs()` |
| コンテナ内生成 | `builtin_mcp.py:create_openapi_mcp_servers()` |
| OpenAPI → MCP ツール変換 | `openapi_mcp.py:create_openapi_mcp_server()` |

## ストリーミング入力モード

SDK MCP サーバー使用時は `query()` の prompt に async generator（ストリーミング入力モード）が必要。通常の文字列を渡すと MCP ツールが登録されず `"No such tool available"` エラーになる。

```python
if has_mcp_servers:
    prompt_input = _create_streaming_prompt(request.user_input)
else:
    prompt_input = request.user_input
```

実装: `workspace_agent/sdk_client.py:execute_streaming()`

## allowed_tools

`execute_service.py:_compute_allowed_tools()` で計算し、ワイルドカード形式でコンテナに渡す。

- `mcp__file-tools__*`
- `mcp__file-presentation__*`
- `mcp__{server_name}__*`（OpenAPI MCP サーバーごと）

## Init イベントでの確認

コンテナから返される `init` イベントの `tools` 配列に `mcp__<server>__<tool>` 形式で全ツールが含まれていれば正常。
