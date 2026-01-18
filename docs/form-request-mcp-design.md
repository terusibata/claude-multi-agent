# FormRequest MCP ツール設計書

## 概要

ユーザーにフォーム入力を要求するMCPツール。AIがJSON Schemaベースでフォームを定義し、フロントエンドで表示、ユーザー入力後に会話を継続する。

## 背景・動機

### 既存のAskUserQuestionツールの課題

1. **60秒タイムアウト制限**: SDKの`canUseTool`コールバックで処理する必要があり、60秒以内に回答が必要
2. **柔軟性の欠如**: 選択肢ベースの質問のみ対応（最大4つの質問、各2-4選択肢）
3. **複雑なフォーム非対応**: テキスト入力、日付選択、ファイルアップロード等に対応できない

### 本ツールの解決策

- **タイムアウトなし**: 通常の会話メッセージとして処理するため制限なし
- **柔軟なフォーム定義**: JSON Schemaで任意のフォームを定義可能
- **動的検索対応**: autocompleteで外部API検索が可能

---

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────┐
│ 会話フロー                                                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  User: 新しいプロジェクトを作成して                               │
│                                                                 │
│  Claude: プロジェクトの設定を入力してください。                    │
│          [mcp__form__request_form ツール呼び出し]                │
│          → フォームスキーマJSON出力                              │
│                                                                 │
│  --- 会話ターン終了 (result イベント) ---                         │
│                                                                 │
│  [フロントエンド]                                                │
│    ↓ tool_use イベントを検出                                     │
│    ↓ フォームスキーマを解析                                       │
│    ↓ フォームUIをモーダル/インラインで表示                         │
│    ↓ ユーザーが入力                                              │
│    ↓ 送信ボタンクリック                                          │
│                                                                 │
│  User: {"project_name": "my-app", "language": "TypeScript", ...} │
│                                                                 │
│  Claude: 了解しました。TypeScriptでmy-appプロジェクトを作成します。  │
│          [実際の作業開始]                                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## MCP ツール実装

### ツール定義

```python
# mcp_form_server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("form")

@mcp.tool()
def request_form(form_schema: dict) -> dict:
    """
    ユーザーにフォーム入力を要求する。

    このツールはフォーム定義をフロントエンドに渡し、
    ユーザーの入力を待機します。入力結果は次のメッセージとして
    送信されます。

    Args:
        form_schema: フォーム定義（下記スキーマ参照）

    Returns:
        フォーム定義とステータスメッセージ
    """
    return {
        "type": "form_request",
        "schema": form_schema,
        "status": "waiting_for_input",
        "message": "フォーム入力を待機しています。入力完了後、次のメッセージとして送信されます。"
    }
```

### MCPサーバー起動

```python
if __name__ == "__main__":
    mcp.run()
```

---

## フォームスキーマ仕様

### 基本構造

```json
{
  "title": "フォームタイトル",
  "description": "フォームの説明（オプション）",
  "submitLabel": "送信",
  "cancelLabel": "キャンセル",
  "fields": [
    { ... }
  ]
}
```

---

## フィールドタイプ一覧

### 1. text - テキスト入力

```json
{
  "name": "project_name",
  "type": "text",
  "label": "プロジェクト名",
  "placeholder": "例: my-awesome-app",
  "required": true,
  "minLength": 3,
  "maxLength": 50,
  "pattern": "^[a-z0-9-]+$",
  "patternError": "小文字英数字とハイフンのみ使用可能です",
  "suggestions": [
    "my-app",
    "web-service",
    "api-server"
  ]
}
```

| プロパティ | 型 | 説明 |
|-----------|-----|------|
| `suggestions` | `string[]` | AIが提案する入力候補。クリックで入力欄に反映 |

### 2. textarea - 複数行テキスト

```json
{
  "name": "description",
  "type": "textarea",
  "label": "プロジェクト説明",
  "placeholder": "プロジェクトの概要を入力...",
  "rows": 5,
  "maxLength": 1000,
  "suggestions": [
    "このプロジェクトは、ユーザー管理機能を提供するWebアプリケーションです。主な機能として、ユーザー登録、ログイン、プロフィール編集があります。",
    "REST APIを提供するバックエンドサービスです。認証にはJWTを使用し、PostgreSQLをデータストアとして利用します。"
  ]
}
```

| プロパティ | 型 | 説明 |
|-----------|-----|------|
| `suggestions` | `string[]` | AIが提案する文章テンプレート。クリックで入力欄に反映 |

### 3. divider - 区切り線

```json
{
  "type": "divider",
  "label": "詳細設定"
}
```

| プロパティ | 型 | 説明 |
|-----------|-----|------|
| `label` | `string` | 区切り線に表示するセクション名（オプション） |

### 4. heading - 見出し

```json
{
  "type": "heading",
  "text": "基本情報",
  "level": 2,
  "description": "プロジェクトの基本的な情報を入力してください"
}
```

| プロパティ | 型 | 説明 |
|-----------|-----|------|
| `text` | `string` | 見出しテキスト |
| `level` | `number` | 見出しレベル（1-4） |
| `description` | `string` | 見出し下の説明文（オプション） |

### 5. select - 単一選択（固定オプション）

```json
{
  "name": "language",
  "type": "select",
  "label": "プログラミング言語",
  "options": [
    { "value": "python", "label": "Python" },
    { "value": "typescript", "label": "TypeScript" },
    { "value": "go", "label": "Go" }
  ],
  "default": "python",
  "required": true
}
```

### 6. multiselect - 複数選択（固定オプション）

```json
{
  "name": "features",
  "type": "multiselect",
  "label": "機能",
  "options": [
    { "value": "auth", "label": "認証" },
    { "value": "db", "label": "DB連携" },
    { "value": "api", "label": "REST API" },
    { "value": "tests", "label": "テスト" }
  ],
  "minSelect": 1,
  "maxSelect": 3
}
```

### 7. autocomplete - 検索付き単一選択

```json
{
  "name": "assignee",
  "type": "autocomplete",
  "label": "担当者",
  "placeholder": "名前を入力して検索...",
  "searchUrl": "https://api.example.com/users/search",
  "searchParams": {
    "q": "{query}",
    "limit": 10
  },
  "displayField": "name",
  "valueField": "id",
  "minChars": 2,
  "debounceMs": 300,
  "renderTemplate": "{name} ({email})",
  "required": true
}
```

### 8. multi-autocomplete - 検索付き複数選択

```json
{
  "name": "members",
  "type": "multi-autocomplete",
  "label": "チームメンバー",
  "searchUrl": "https://api.example.com/users/search",
  "displayField": "name",
  "valueField": "id",
  "maxSelect": 10
}
```

### 9. cascading-select - 連動選択

```json
{
  "name": "prefecture",
  "type": "cascading-select",
  "label": "都道府県",
  "searchUrl": "https://api.example.com/prefectures",
  "dependsOn": "country",
  "dependsOnParam": "country_code",
  "displayField": "name",
  "valueField": "code"
}
```

### 10. async-select - 非同期読み込み選択

```json
{
  "name": "category",
  "type": "async-select",
  "label": "カテゴリ",
  "loadUrl": "https://api.example.com/categories",
  "displayField": "name",
  "valueField": "id"
}
```

### 11. checkbox - チェックボックス

```json
{
  "name": "agree_terms",
  "type": "checkbox",
  "label": "利用規約に同意する",
  "required": true
}
```

### 12. radio - ラジオボタン

```json
{
  "name": "priority",
  "type": "radio",
  "label": "優先度",
  "options": [
    { "value": "high", "label": "高" },
    { "value": "medium", "label": "中" },
    { "value": "low", "label": "低" }
  ],
  "default": "medium"
}
```

### 13. number - 数値入力

```json
{
  "name": "quantity",
  "type": "number",
  "label": "数量",
  "min": 1,
  "max": 100,
  "step": 1,
  "default": 1
}
```

### 14. range - スライダー

```json
{
  "name": "budget_percentage",
  "type": "range",
  "label": "予算配分 (%)",
  "min": 0,
  "max": 100,
  "step": 5,
  "showValue": true
}
```

### 15. date - 日付選択

```json
{
  "name": "due_date",
  "type": "date",
  "label": "期限",
  "minDate": "today",
  "maxDate": "+30days"
}
```

### 16. datetime - 日時選択

```json
{
  "name": "meeting_time",
  "type": "datetime",
  "label": "ミーティング日時"
}
```

### 17. file - ファイルアップロード

```json
{
  "name": "attachment",
  "type": "file",
  "label": "添付ファイル",
  "accept": ".pdf,.doc,.docx",
  "maxSize": 10485760,
  "multiple": false
}
```

### 18. hidden - 隠しフィールド

```json
{
  "name": "form_version",
  "type": "hidden",
  "value": "1.0"
}
```

---

## 使用例

### 例1: プロジェクト作成フォーム（suggestions・divider使用）

```json
{
  "title": "新規プロジェクト作成",
  "description": "プロジェクトの基本情報を入力してください",
  "fields": [
    {
      "type": "heading",
      "text": "基本情報",
      "level": 2
    },
    {
      "name": "project_name",
      "type": "text",
      "label": "プロジェクト名",
      "required": true,
      "pattern": "^[a-z0-9-]+$",
      "suggestions": [
        "my-web-app",
        "api-service",
        "data-pipeline"
      ]
    },
    {
      "name": "language",
      "type": "select",
      "label": "言語",
      "options": [
        { "value": "python", "label": "Python" },
        { "value": "typescript", "label": "TypeScript" },
        { "value": "go", "label": "Go" }
      ],
      "default": "python"
    },
    {
      "type": "divider",
      "label": "詳細設定"
    },
    {
      "name": "features",
      "type": "multiselect",
      "label": "機能",
      "options": [
        { "value": "auth", "label": "認証" },
        { "value": "db", "label": "DB連携" },
        { "value": "api", "label": "REST API" }
      ]
    },
    {
      "name": "description",
      "type": "textarea",
      "label": "説明",
      "rows": 3,
      "suggestions": [
        "ユーザー認証機能を持つWebアプリケーション。JWT認証、ソーシャルログイン対応。",
        "マイクロサービス間の通信を担うAPIゲートウェイ。認証・認可・レート制限機能付き。",
        "バッチ処理用のデータパイプライン。S3からデータを取得し、変換後にDWHへ格納。"
      ]
    }
  ]
}
```

### 例2: 担当者アサインフォーム（動的検索）

```json
{
  "title": "タスク担当者設定",
  "fields": [
    {
      "name": "assignee",
      "type": "autocomplete",
      "label": "担当者",
      "searchUrl": "https://api.example.com/users/search",
      "searchParams": { "q": "{query}", "limit": 10 },
      "displayField": "name",
      "valueField": "id",
      "renderTemplate": "{name} ({department})",
      "required": true
    },
    {
      "name": "reviewers",
      "type": "multi-autocomplete",
      "label": "レビュアー",
      "searchUrl": "https://api.example.com/users/search",
      "displayField": "name",
      "valueField": "id",
      "maxSelect": 3
    },
    {
      "type": "divider"
    },
    {
      "name": "due_date",
      "type": "date",
      "label": "期限",
      "minDate": "today"
    },
    {
      "name": "notes",
      "type": "textarea",
      "label": "備考",
      "rows": 2,
      "suggestions": [
        "優先度高。今週中に対応をお願いします。",
        "次回スプリントで対応予定。急ぎではありません。"
      ]
    }
  ]
}
```

### 例3: 住所入力フォーム（連動選択）

```json
{
  "title": "住所入力",
  "fields": [
    {
      "name": "country",
      "type": "async-select",
      "label": "国",
      "loadUrl": "https://api.example.com/countries",
      "displayField": "name",
      "valueField": "code",
      "default": "JP"
    },
    {
      "name": "prefecture",
      "type": "cascading-select",
      "label": "都道府県",
      "searchUrl": "https://api.example.com/prefectures",
      "dependsOn": "country",
      "dependsOnParam": "country_code",
      "displayField": "name",
      "valueField": "code"
    },
    {
      "name": "city",
      "type": "cascading-select",
      "label": "市区町村",
      "searchUrl": "https://api.example.com/cities",
      "dependsOn": "prefecture",
      "dependsOnParam": "prefecture_code",
      "displayField": "name",
      "valueField": "code"
    },
    {
      "name": "address",
      "type": "text",
      "label": "番地・建物名"
    }
  ]
}
```

---

## Agent Skills への記載例

```markdown
## FormRequest ツール

ユーザーにフォーム入力を求める場合は `mcp__form__request_form` ツールを使用してください。

### 利用可能な検索エンドポイント

| 用途 | URL | パラメータ |
|------|-----|-----------|
| ユーザー検索 | `https://api.example.com/users/search` | `q`: 検索クエリ, `limit`: 件数 |
| プロジェクト検索 | `https://api.example.com/projects/search` | `q`: 検索クエリ |
| カテゴリ一覧 | `https://api.example.com/categories` | なし |
| 都道府県一覧 | `https://api.example.com/prefectures` | `country_code`: 国コード |

### 使用ガイドライン

1. 複数の情報が必要な場合はフォームを使用
2. 単純な選択のみの場合は通常の会話で確認
3. 検索が必要なフィールドには `autocomplete` タイプを使用
4. テキスト入力には `suggestions` で入力例を提示すると親切
5. フォームが長くなる場合は `divider` や `heading` でセクション分け
```

---

## 実装チェックリスト

### バックエンド（MCP サーバー）

- [ ] FastMCP でツール定義
- [ ] `request_form` ツール実装
- [ ] MCPサーバー設定ファイル追加
- [ ] テナントへのMCPサーバー登録

### テスト

- [ ] 各フィールドタイプの動作確認
- [ ] autocomplete の検索動作確認
- [ ] cascading-select の連動動作確認
- [ ] suggestions のクリック動作確認
- [ ] divider/heading の表示確認
- [ ] バリデーションエラー表示確認
- [ ] 送信データの形式確認

---

## 関連ドキュメント

- [Claude Agent SDK - MCP Servers](https://docs.anthropic.com/en/docs/claude-code/mcp)
- [MCP (Model Context Protocol) 仕様](https://modelcontextprotocol.io/)
- [既存プロジェクトのMCPサーバー実装](../app/services/mcp_server_service.py)
