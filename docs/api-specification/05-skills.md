# Agent Skills管理API

Agent Skills（AIエージェントの能力拡張）の管理を行うAPIです。

## 概要

| 項目 | 値 |
|------|-----|
| ベースパス | `/api/tenants/{tenant_id}/skills` |
| 認証 | 必要 |
| スコープ | テナント単位 |

### Agent Skillsとは

Agent Skillsは、AIエージェントに特定の能力やドメイン知識を追加する仕組みです。
各Skillは`SKILL.md`ファイルと追加リソースファイルで構成されます。

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/tenants/{tenant_id}/skills` | Skills一覧取得 |
| GET | `/api/tenants/{tenant_id}/skills/slash-commands` | スラッシュコマンド一覧 |
| GET | `/api/tenants/{tenant_id}/skills/{skill_id}` | Skill詳細取得 |
| POST | `/api/tenants/{tenant_id}/skills` | Skillアップロード |
| PUT | `/api/tenants/{tenant_id}/skills/{skill_id}` | Skillメタデータ更新 |
| PUT | `/api/tenants/{tenant_id}/skills/{skill_id}/files` | Skillファイル更新 |
| DELETE | `/api/tenants/{tenant_id}/skills/{skill_id}` | Skill削除 |
| GET | `/api/tenants/{tenant_id}/skills/{skill_id}/files` | Skillファイル一覧 |
| GET | `/api/tenants/{tenant_id}/skills/{skill_id}/files/{path}` | Skillファイル内容取得 |

---

## データ型

### SkillResponse

```typescript
interface SkillResponse {
  skill_id: string;                     // SkillのID（UUID）
  tenant_id: string;                    // テナントID
  name: string;                         // Skill名（識別子）
  display_title: string | null;         // 表示タイトル
  description: string | null;           // 説明
  version: number;                      // バージョン番号
  file_path: string;                    // ファイルシステムパス
  status: "active" | "inactive";        // ステータス
  slash_command: string | null;         // スラッシュコマンド名
  slash_command_description: string | null; // スラッシュコマンドの説明
  is_user_selectable: boolean;          // ユーザー選択可能フラグ
  created_at: string;                   // 作成日時
  updated_at: string;                   // 更新日時
}
```

### SlashCommandItem

```typescript
interface SlashCommandItem {
  skill_id: string;           // SkillのID
  name: string;               // Skill名（preferred_skillsに渡す値）
  slash_command: string;      // スラッシュコマンド表示名
  description: string | null; // 説明
}
```

### SkillFileInfo

```typescript
interface SkillFileInfo {
  filename: string;       // ファイル名
  path: string;           // パス
  size: number;           // ファイルサイズ（バイト）
  modified_at: string;    // 更新日時
}
```

---

## GET /api/tenants/{tenant_id}/skills

テナントのAgent Skills一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `status` | string | No | - | ステータスフィルター (`active` / `inactive`) |

### レスポンス

**成功時 (200 OK)**

```json
[
  {
    "skill_id": "skill-001",
    "tenant_id": "acme-corp",
    "name": "data-analysis",
    "display_title": "データ分析",
    "description": "CSVやExcelファイルのデータ分析を支援します",
    "version": 2,
    "file_path": "/skills/acme-corp/data-analysis",
    "status": "active",
    "slash_command": "データ分析",
    "slash_command_description": "データファイルを分析",
    "is_user_selectable": true,
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-16T14:00:00Z"
  }
]
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/skills" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/skills/slash-commands

ユーザーが選択可能なスラッシュコマンド一覧を取得します。
フロントエンドのオートコンプリート機能で使用します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### レスポンス

**成功時 (200 OK)**

```json
{
  "items": [
    {
      "skill_id": "skill-001",
      "name": "data-analysis",
      "slash_command": "データ分析",
      "description": "データファイルを分析"
    },
    {
      "skill_id": "skill-002",
      "name": "code-review",
      "slash_command": "コードレビュー",
      "description": "コードの品質チェック"
    }
  ]
}
```

### フロントエンドでの使用方法

1. このAPIでスラッシュコマンド一覧を取得
2. ユーザーが`/`を入力したらオートコンプリートを表示
3. 選択されたSkillの`name`を`preferred_skills`パラメータに渡す

```typescript
// ストリーミングリクエスト例
const request = {
  user_input: "このファイルを分析してください",
  executor: { ... },
  preferred_skills: ["data-analysis"]  // ← nameフィールドの値
};
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/skills/slash-commands" \
  -H "X-API-Key: your_api_key"
```

---

## POST /api/tenants/{tenant_id}/skills

新しいSkillをアップロードします。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |

### リクエストボディ（multipart/form-data）

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | string | Yes | Skill名（英数字、ハイフン、アンダースコアのみ） |
| `display_title` | string | No | 表示タイトル（最大300文字） |
| `description` | string | No | 説明 |
| `skill_md` | File | Yes | SKILL.mdファイル |
| `additional_files` | File[] | No | 追加リソースファイル |

### Skill名の制約

- 英数字、ハイフン(`-`)、アンダースコア(`_`)のみ使用可能
- パターン: `^[a-zA-Z0-9_\-]+$`
- スペース、スラッシュ、ドット等は使用不可

### レスポンス

**成功時 (201 Created)**

```json
{
  "skill_id": "skill-003",
  "tenant_id": "acme-corp",
  "name": "new-skill",
  "display_title": "新しいスキル",
  "description": "新しいスキルの説明",
  "version": 1,
  "file_path": "/skills/acme-corp/new-skill",
  "status": "active",
  "slash_command": null,
  "slash_command_description": null,
  "is_user_selectable": true,
  "created_at": "2024-01-17T09:00:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

**エラー: 重複 (409 Conflict)**

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "Skill 'new-skill' は既に存在します"
  }
}
```

**エラー: エンコーディング (400 Bad Request)**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "ファイル 'data.txt' はUTF-8でエンコードされていません"
  }
}
```

### curlの例

```bash
# SKILL.mdのみ
curl -X POST "https://api.example.com/api/tenants/acme-corp/skills" \
  -H "X-API-Key: your_api_key" \
  -F "name=data-analysis" \
  -F "display_title=データ分析" \
  -F "description=データファイルの分析を支援" \
  -F "skill_md=@SKILL.md"

# 追加ファイル付き
curl -X POST "https://api.example.com/api/tenants/acme-corp/skills" \
  -H "X-API-Key: your_api_key" \
  -F "name=custom-skill" \
  -F "skill_md=@SKILL.md" \
  -F "additional_files=@templates/report.md" \
  -F "additional_files=@examples/sample.json"
```

---

## PUT /api/tenants/{tenant_id}/skills/{skill_id}

Skillのメタデータを更新します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `skill_id` | string | Yes | SkillのID |

### リクエストボディ

```typescript
interface SkillUpdate {
  display_title?: string;              // 表示タイトル（最大300文字）
  description?: string;                // 説明
  status?: "active" | "inactive";      // ステータス
  slash_command?: string;              // スラッシュコマンド名（最大100文字）
  slash_command_description?: string;  // スラッシュコマンドの説明（最大500文字）
  is_user_selectable?: boolean;        // ユーザー選択可能フラグ
}
```

### レスポンス

**成功時 (200 OK)**

```json
{
  "skill_id": "skill-001",
  "tenant_id": "acme-corp",
  "name": "data-analysis",
  "display_title": "高度なデータ分析",
  "description": "更新された説明",
  "version": 2,
  "file_path": "/skills/acme-corp/data-analysis",
  "status": "active",
  "slash_command": "データ分析Pro",
  "slash_command_description": "高度なデータ分析を実行",
  "is_user_selectable": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-17T09:00:00Z"
}
```

### curlの例

```bash
curl -X PUT "https://api.example.com/api/tenants/acme-corp/skills/skill-001" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "display_title": "高度なデータ分析",
    "slash_command": "データ分析Pro",
    "slash_command_description": "高度なデータ分析を実行"
  }'
```

---

## PUT /api/tenants/{tenant_id}/skills/{skill_id}/files

Skillのファイルを更新します。バージョンが上がります。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `skill_id` | string | Yes | SkillのID |

### リクエストボディ（multipart/form-data）

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `files` | File[] | Yes | 更新するファイル |

### レスポンス

**成功時 (200 OK)**

```json
{
  "skill_id": "skill-001",
  "tenant_id": "acme-corp",
  "name": "data-analysis",
  "version": 3,
  ...
}
```

### curlの例

```bash
curl -X PUT "https://api.example.com/api/tenants/acme-corp/skills/skill-001/files" \
  -H "X-API-Key: your_api_key" \
  -F "files=@SKILL.md" \
  -F "files=@templates/updated.md"
```

---

## DELETE /api/tenants/{tenant_id}/skills/{skill_id}

Skillを削除します（ファイルシステムからも削除）。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `skill_id` | string | Yes | SkillのID |

### レスポンス

**成功時 (204 No Content)**

レスポンスボディなし

### curlの例

```bash
curl -X DELETE "https://api.example.com/api/tenants/acme-corp/skills/skill-001" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/skills/{skill_id}/files

Skillのファイル一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `skill_id` | string | Yes | SkillのID |

### レスポンス

**成功時 (200 OK)**

```json
{
  "skill_id": "skill-001",
  "skill_name": "data-analysis",
  "files": [
    {
      "filename": "SKILL.md",
      "path": "SKILL.md",
      "size": 2048,
      "modified_at": "2024-01-16T14:00:00Z"
    },
    {
      "filename": "report.md",
      "path": "templates/report.md",
      "size": 512,
      "modified_at": "2024-01-16T14:00:00Z"
    }
  ]
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/skills/skill-001/files" \
  -H "X-API-Key: your_api_key"
```

---

## GET /api/tenants/{tenant_id}/skills/{skill_id}/files/{file_path}

Skillの特定ファイルの内容を取得します。

### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `tenant_id` | string | Yes | テナントID |
| `skill_id` | string | Yes | SkillのID |
| `file_path` | string | Yes | ファイルパス（例: `templates/report.md`） |

### レスポンス

**成功時 (200 OK)**

```json
{
  "content": "# SKILL.md\n\nこのスキルは..."
}
```

### curlの例

```bash
curl -X GET "https://api.example.com/api/tenants/acme-corp/skills/skill-001/files/SKILL.md" \
  -H "X-API-Key: your_api_key"

# サブディレクトリ内のファイル
curl -X GET "https://api.example.com/api/tenants/acme-corp/skills/skill-001/files/templates/report.md" \
  -H "X-API-Key: your_api_key"
```

---

## SKILL.mdの構造（参考）

```markdown
# スキル名

## 概要
このスキルの説明

## 使用方法
ユーザーへの指示

## 例
使用例

## 注意事項
制約や注意点
```

---

## 関連API

- [ストリーミングAPI](./04-streaming.md) - `preferred_skills`でSkillを使用
- [テナント管理API](./01-tenants.md) - テナント設定
