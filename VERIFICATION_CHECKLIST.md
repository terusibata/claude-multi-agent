# コンテナ新規作成時の検証チェックリスト

このドキュメントでは、ワークスペースコンテナ新規作成時に、スキルとMCPサーバーが正しく利用可能になっているかを確認する方法を説明します。

## 1. スキル同期の確認

### 期待される動作

1. **ホスト側のスキル保存場所**
   ```
   {skills_base_path}/tenant_{tenant_id}/.claude/skills/{skill_name}/
   ```

2. **コンテナ側の同期先**
   ```
   /workspace/.claude/skills/{skill_name}/
   ```

3. **SDK設定**
   - `cwd`: `/workspace`
   - `setting_sources`: `["project"]` (スキル同期成功時)
   - Claude Code SDKは `/workspace/.claude/skills/` からスキルを読み込む

### 確認方法

#### A. ログでの確認

`execute_service.py` のログ出力を確認:
```
スキル同期完了: tenant_id=xxx, files_synced=true
```

#### B. コンテナ内での確認

コンテナにアクセスして実際のファイル構造を確認:
```bash
docker exec -it <container_id> ls -la /workspace/.claude/skills/
```

期待される出力:
```
drwxr-xr-x  2 appuser appuser 4096 Feb 15 10:00 my-skill
drwxr-xr-x  2 appuser appuser 4096 Feb 15 10:00 another-skill
```

#### C. 実行時の確認

エージェント実行時のログで `setting_sources` が設定されていることを確認:
```json
{
  "container_request": {
    "setting_sources": ["project"]
  }
}
```

---

## 2. ビルトイン MCP サーバーの確認

### file-presentation サーバー

**ツール**: `mcp__file-presentation__present_files`

**確認方法**:

1. **Init イベントの確認**
   ```json
   {
     "event": "init",
     "data": {
       "tools": [
         "Task", "Bash", "Read", "Write",
         "mcp__file-presentation__present_files"
       ]
     }
   }
   ```

2. **ツール実行テスト**
   ```
   ユーザー: "test.txtファイルを作成して提示してください"
   ```

   期待される動作:
   - `Write` ツールでファイル作成
   - `mcp__file-presentation__present_files` が呼び出される
   - ファイル情報が表示される

3. **ログでの確認**
   ```
   MCP servers created: ['file-presentation', 'file-tools']
   SDK実行開始: mcp_servers=['file-presentation', 'file-tools']
   ```

### file-tools サーバー

**ツール** (15個):
- `mcp__file-tools__list_workspace_files`
- `mcp__file-tools__read_image_file`
- `mcp__file-tools__get_sheet_info`
- `mcp__file-tools__get_sheet_csv`
- `mcp__file-tools__search_workbook`
- `mcp__file-tools__inspect_pdf_file`
- `mcp__file-tools__read_pdf_pages`
- `mcp__file-tools__convert_pdf_to_images`
- `mcp__file-tools__get_document_info`
- `mcp__file-tools__get_document_content`
- `mcp__file-tools__search_document`
- `mcp__file-tools__get_presentation_info`
- `mcp__file-tools__get_slides_content`
- `mcp__file-tools__search_presentation`
- `mcp__file-tools__inspect_image_file`

**確認方法**:

1. **Init イベントでツールリストを確認**
   全15個のツールが `mcp__file-tools__*` という形式で含まれているか

2. **ツール実行テスト**
   ```
   ユーザー: "ワークスペース内のファイル一覧を表示してください"
   ```

   期待される動作:
   - `mcp__file-tools__list_workspace_files` が呼び出される
   - ファイル一覧が返される

---

## 3. OpenAPI MCP サーバーの確認

### 登録フロー

1. **データベース確認**
   ```sql
   SELECT name, status, openapi_base_url
   FROM mcp_servers
   WHERE tenant_id = '...' AND status = 'active';
   ```

2. **ログ確認**
   ```
   MCP サーバー設定取得: count=N
   OpenAPI MCP server creation: server_name=xxx
   ```

### Init イベントでの確認

```json
{
  "event": "init",
  "data": {
    "tools": [
      "mcp__my-api__get_data",
      "mcp__my-api__post_data"
    ],
    "mcp_servers": [
      {
        "name": "my-api",
        "status": "connected",
        "tools": ["get_data", "post_data"]
      }
    ]
  }
}
```

### 確認ポイント

- [ ] `status` が `"connected"` になっている
- [ ] `tools` 配列にツール名が含まれている
- [ ] ツール名が `mcp__{server_name}__{tool_name}` 形式で init の tools リストに含まれている

---

## 4. ストリーミング入力モードの確認

### 修正内容 (2026-02-15)

SDK MCP サーバー使用時は、`query()` の `prompt` パラメータに**ストリーミング入力モード**（async generator）が必要。

**修正前**:
```python
async for message in query(prompt=request.user_input, options=options):
    # → MCP ツールが登録されず "No such tool available" エラー
```

**修正後**:
```python
if has_mcp_servers:
    prompt_input = _create_streaming_prompt(request.user_input)
else:
    prompt_input = request.user_input

async for message in query(prompt=prompt_input, options=options):
    # → 正常に動作
```

### 確認方法

1. **ログでの確認**
   ```
   SDK実行開始: model=..., cwd=/workspace, mcp_servers=['file-presentation', 'file-tools']
   ```

2. **エラーが出ないことを確認**
   - MCP ツール呼び出し時に `"No such tool available"` エラーが**出ない**こと
   - ツール実行が正常に完了すること

---

## 5. 統合テストシナリオ

### シナリオ 1: ファイル作成と提示

**操作**:
```
ユーザー: "hello.txtファイルを作成し、'Hello World'と書き込んで提示してください"
```

**期待される動作**:
1. `Write` ツールで `/workspace/hello.txt` を作成
2. `mcp__file-presentation__present_files` が呼び出される
3. ファイル情報が表示される

**確認項目**:
- [ ] ファイルが作成されている
- [ ] MCP ツールが呼び出されている
- [ ] エラーが発生していない

### シナリオ 2: Excel ファイル読み込み

**前提**: `/workspace/data.xlsx` が存在

**操作**:
```
ユーザー: "data.xlsxの内容を確認してください"
```

**期待される動作**:
1. `mcp__file-tools__list_workspace_files` でファイル確認
2. `mcp__file-tools__get_sheet_info` でシート情報取得
3. `mcp__file-tools__get_sheet_csv` でデータ取得

**確認項目**:
- [ ] 各 MCP ツールが順次呼び出される
- [ ] データが正しく読み込まれる
- [ ] エラーが発生していない

### シナリオ 3: スキル実行

**前提**: スキルが `/workspace/.claude/skills/` に同期されている

**操作**:
```
ユーザー: "/my-skill を実行してください"
```

**期待される動作**:
1. `Skill` ツールが呼び出される
2. スキルが正常に実行される

**確認項目**:
- [ ] `setting_sources: ["project"]` が設定されている
- [ ] スキルファイルがコンテナ内に存在する
- [ ] スキル実行が成功する

---

## 6. トラブルシューティング

### MCP ツールが呼び出されない

**症状**: Init イベントに MCP ツールが含まれない

**確認項目**:
1. コンテナログで MCP サーバー作成エラーがないか確認
   ```
   MCP server creation failed: ...
   ```

2. ストリーミング入力モードが使用されているか確認
   ```python
   # sdk_client.py の execute_streaming() 関数
   if has_mcp_servers:
       prompt_input = _create_streaming_prompt(request.user_input)
   ```

3. `builtin_mcp.py` のインポートエラーがないか確認
   ```
   claude_agent_sdk not available, skipping file-presentation MCP server
   ```

### MCP ツール呼び出しでエラー

**症状**: `"No such tool available: mcp__file-presentation__present_files"`

**原因**: ストリーミング入力モードが使用されていない

**修正**: `workspace_agent/sdk_client.py` の最新版を使用

### スキルが見つからない

**症状**: `Skill tool not found` または スキル実行エラー

**確認項目**:
1. スキルファイルが同期されているか
   ```bash
   docker exec -it <container_id> ls -la /workspace/.claude/skills/
   ```

2. `setting_sources` が設定されているか
   ```json
   {
     "setting_sources": ["project"]
   }
   ```

3. スキル同期エラーログがないか
   ```
   スキル同期エラー: error=...
   ```

---

## 7. 成功時の期待されるログ出力

### コンテナ起動時
```
[INFO] ワークスペースエージェント起動: socket=/var/run/ws/agent.sock
[INFO] MCP servers created: ['file-presentation', 'file-tools']
[INFO] SDK実行開始: model=..., cwd=/workspace, mcp_servers=['file-presentation', 'file-tools']
```

### Init イベント
```json
{
  "event": "init",
  "seq": 1,
  "tools": [
    "Task", "Bash", "Read", "Write", "Edit",
    "mcp__file-presentation__present_files",
    "mcp__file-tools__list_workspace_files",
    "mcp__file-tools__read_image_file",
    ...
  ],
  "session_id": "...",
  "model": "..."
}
```

### MCP ツール呼び出し時
```json
{
  "event": "tool_call",
  "tool_name": "mcp__file-presentation__present_files",
  "input": {
    "file_paths": ["hello.txt"],
    "description": "Created hello.txt file"
  }
}
```

### MCP ツール実行結果
```json
{
  "event": "tool_result",
  "tool_name": "mcp__file-presentation__present_files",
  "status": "completed",
  "content": "ファイルを提示しました: ...",
  "is_error": false
}
```

---

## まとめ

✅ **スキル**:
- `/workspace/.claude/skills/` に同期
- `setting_sources: ["project"]` で Claude Code SDK が認識

✅ **ビルトイン MCP サーバー**:
- `file-presentation` (1ツール)
- `file-tools` (15ツール)
- ストリーミング入力モードで正常動作

✅ **OpenAPI MCP サーバー**:
- テナントDBから自動取得
- `create_openapi_mcp_server()` で作成
- Init イベントにツール名が含まれる

✅ **Init イベント**:
- 全MCP ツールが `mcp__<server>__<tool>` 形式で含まれる
- `mcp_servers` フィールドにサーバー情報が含まれる

すべての設定が正しく動作し、コンテナ新規作成時にスキルとMCPサーバーが利用可能になっています。
