# FormRequest MCP ツール仕様書

## 概要

FormRequest MCPツールは、AIがユーザーにフォーム入力を要求するための機能です。JSON Schemaベースでフォームを定義し、フロントエンドで動的にフォームUIを生成・表示することで、複雑なデータ入力を実現します。

## 目次

1. [背景・動機](#背景動機)
2. [アーキテクチャ](#アーキテクチャ)
3. [ツール仕様](#ツール仕様)
4. [フォームスキーマ仕様](#フォームスキーマ仕様)
5. [フィールドタイプ一覧](#フィールドタイプ一覧)
6. [フロントエンド実装ガイド](#フロントエンド実装ガイド)
7. [使用例](#使用例)
8. [Agent Skills への記載例](#agent-skills-への記載例)

---

## 背景・動機

### 既存のAskUserQuestionツールの課題

| 課題 | 詳細 |
|------|------|
| **60秒タイムアウト制限** | SDKの`canUseTool`コールバックで処理する必要があり、60秒以内に回答が必要 |
| **柔軟性の欠如** | 選択肢ベースの質問のみ対応（最大4つの質問、各2-4選択肢） |
| **複雑なフォーム非対応** | テキスト入力、日付選択、ファイルアップロード等に対応できない |

### 本ツールの解決策

| 特徴 | 詳細 |
|------|------|
| **タイムアウトなし** | 通常の会話メッセージとして処理するため制限なし |
| **柔軟なフォーム定義** | JSON Schemaで任意のフォームを定義可能 |
| **動的検索対応** | autocompleteで外部API検索が可能 |
| **リッチなUI** | 日付ピッカー、スライダー、ファイルアップロード等に対応 |

---

## アーキテクチャ

### 会話フロー

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

### シーケンス図

```
┌──────────┐     ┌──────────────┐     ┌───────────┐     ┌──────────┐
│  User    │     │  Frontend    │     │  Backend  │     │  Claude  │
└────┬─────┘     └──────┬───────┘     └─────┬─────┘     └────┬─────┘
     │                  │                   │                │
     │  "プロジェクト作成" │                   │                │
     │─────────────────>│                   │                │
     │                  │  execute API     │                │
     │                  │─────────────────>│                │
     │                  │                   │  Agent実行    │
     │                  │                   │───────────────>│
     │                  │                   │                │
     │                  │                   │  tool_use      │
     │                  │                   │  (request_form)│
     │                  │                   │<───────────────│
     │                  │  SSEイベント       │                │
     │                  │  (tool_use)      │                │
     │                  │<─────────────────│                │
     │                  │                   │                │
     │  フォーム表示     │                   │                │
     │<─────────────────│                   │                │
     │                  │                   │                │
     │  入力・送信      │                   │                │
     │─────────────────>│                   │                │
     │                  │  execute API     │                │
     │                  │  (フォームデータ)  │                │
     │                  │─────────────────>│                │
     │                  │                   │  継続実行      │
     │                  │                   │───────────────>│
     │                  │                   │                │
```

---

## ツール仕様

### ツール名

```
mcp__form__request_form
```

### 入力パラメータ

| パラメータ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `form_schema` | `object` | Yes | フォーム定義スキーマ |

### 出力フォーマット

ツール実行結果は以下の形式で返されます：

```json
{
  "content": [
    {
      "type": "text",
      "text": "フォーム入力を待機しています。\n\n【新規プロジェクト作成】\nプロジェクトの基本情報を入力してください\n\nフィールド数: 5\nユーザーがフォームに入力後、次のメッセージとして入力内容が送信されます。"
    }
  ],
  "_metadata": {
    "type": "form_request",
    "schema": { /* フォームスキーマ全体 */ },
    "status": "waiting_for_input"
  }
}
```

### フロントエンドでの検出

SSEストリームで受信する`tool_use`イベントから、以下の条件でフォームリクエストを検出します：

```javascript
// tool_use イベントの検出
if (event.type === 'tool_use' && event.name === 'mcp__form__request_form') {
  const formSchema = event.input.form_schema;
  // フォームUIを表示
  showFormModal(formSchema);
}
```

または、`tool_result`イベントの`_metadata`から検出：

```javascript
if (toolResult._metadata?.type === 'form_request') {
  const formSchema = toolResult._metadata.schema;
  showFormModal(formSchema);
}
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
    { /* フィールド定義 */ }
  ]
}
```

### トップレベルプロパティ

| プロパティ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `title` | `string` | Yes | - | フォームのタイトル |
| `description` | `string` | No | - | フォームの説明文 |
| `submitLabel` | `string` | No | `"送信"` | 送信ボタンのラベル |
| `cancelLabel` | `string` | No | `"キャンセル"` | キャンセルボタンのラベル |
| `fields` | `array` | Yes | - | フィールド定義の配列 |

---

## フィールドタイプ一覧

### 1. text - テキスト入力

単一行のテキスト入力フィールド。

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
  "default": "",
  "suggestions": [
    "my-app",
    "web-service",
    "api-server"
  ]
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名（送信データのキー） |
| `type` | `"text"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `placeholder` | `string` | No | プレースホルダーテキスト |
| `required` | `boolean` | No | 必須フィールドかどうか |
| `minLength` | `number` | No | 最小文字数 |
| `maxLength` | `number` | No | 最大文字数 |
| `pattern` | `string` | No | 正規表現パターン |
| `patternError` | `string` | No | パターンエラー時のメッセージ |
| `default` | `string` | No | デフォルト値 |
| `suggestions` | `string[]` | No | AIが提案する入力候補 |

### 2. textarea - 複数行テキスト

複数行のテキスト入力フィールド。

```json
{
  "name": "description",
  "type": "textarea",
  "label": "プロジェクト説明",
  "placeholder": "プロジェクトの概要を入力...",
  "rows": 5,
  "maxLength": 1000,
  "suggestions": [
    "このプロジェクトは、ユーザー管理機能を提供するWebアプリケーションです。",
    "REST APIを提供するバックエンドサービスです。"
  ]
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"textarea"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `placeholder` | `string` | No | プレースホルダーテキスト |
| `rows` | `number` | No | 表示行数（デフォルト: 3） |
| `maxLength` | `number` | No | 最大文字数 |
| `suggestions` | `string[]` | No | AIが提案する文章テンプレート |

### 3. divider - 区切り線

フォームのセクションを区切る装飾要素。

```json
{
  "type": "divider",
  "label": "詳細設定"
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `type` | `"divider"` | Yes | フィールドタイプ |
| `label` | `string` | No | 区切り線に表示するセクション名 |

### 4. heading - 見出し

セクションの見出しテキスト。

```json
{
  "type": "heading",
  "text": "基本情報",
  "level": 2,
  "description": "プロジェクトの基本的な情報を入力してください"
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `type` | `"heading"` | Yes | フィールドタイプ |
| `text` | `string` | Yes | 見出しテキスト |
| `level` | `number` | No | 見出しレベル（1-4、デフォルト: 2） |
| `description` | `string` | No | 見出し下の説明文 |

### 5. select - 単一選択（固定オプション）

ドロップダウンによる単一選択。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"select"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `options` | `array` | Yes | 選択肢の配列 |
| `options[].value` | `string` | Yes | 選択肢の値 |
| `options[].label` | `string` | Yes | 選択肢の表示名 |
| `default` | `string` | No | デフォルト値 |
| `required` | `boolean` | No | 必須フィールドかどうか |

### 6. multiselect - 複数選択（固定オプション）

複数項目を選択可能なフィールド。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"multiselect"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `options` | `array` | Yes | 選択肢の配列 |
| `minSelect` | `number` | No | 最小選択数 |
| `maxSelect` | `number` | No | 最大選択数 |

### 7. autocomplete - 検索付き単一選択

外部APIを使用した動的検索・選択。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"autocomplete"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `placeholder` | `string` | No | プレースホルダーテキスト |
| `searchUrl` | `string` | Yes | 検索API URL |
| `searchParams` | `object` | No | 検索パラメータ（`{query}`がユーザー入力に置換） |
| `displayField` | `string` | Yes | 表示に使用するフィールド名 |
| `valueField` | `string` | Yes | 値として使用するフィールド名 |
| `minChars` | `number` | No | 検索開始の最小文字数（デフォルト: 1） |
| `debounceMs` | `number` | No | 入力デバウンス時間（ミリ秒、デフォルト: 300） |
| `renderTemplate` | `string` | No | 表示テンプレート（`{field}`形式） |
| `required` | `boolean` | No | 必須フィールドかどうか |

### 8. multi-autocomplete - 検索付き複数選択

外部APIを使用した動的検索・複数選択。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"multi-autocomplete"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `searchUrl` | `string` | Yes | 検索API URL |
| `displayField` | `string` | Yes | 表示に使用するフィールド名 |
| `valueField` | `string` | Yes | 値として使用するフィールド名 |
| `maxSelect` | `number` | No | 最大選択数 |

### 9. cascading-select - 連動選択

親フィールドの値に基づいて選択肢が変わる連動選択。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"cascading-select"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `searchUrl` | `string` | Yes | 検索API URL |
| `dependsOn` | `string` | Yes | 依存する親フィールドの名前 |
| `dependsOnParam` | `string` | Yes | APIに渡すパラメータ名 |
| `displayField` | `string` | Yes | 表示に使用するフィールド名 |
| `valueField` | `string` | Yes | 値として使用するフィールド名 |

### 10. async-select - 非同期読み込み選択

ページ読み込み時にAPIから選択肢を取得。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"async-select"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `loadUrl` | `string` | Yes | データ取得API URL |
| `displayField` | `string` | Yes | 表示に使用するフィールド名 |
| `valueField` | `string` | Yes | 値として使用するフィールド名 |

### 11. checkbox - チェックボックス

単一のチェックボックス（真偽値）。

```json
{
  "name": "agree_terms",
  "type": "checkbox",
  "label": "利用規約に同意する",
  "required": true
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"checkbox"` | Yes | フィールドタイプ |
| `label` | `string` | Yes | チェックボックスのラベル |
| `required` | `boolean` | No | チェック必須かどうか |
| `default` | `boolean` | No | デフォルト状態 |

### 12. radio - ラジオボタン

排他的な選択（ラジオボタングループ）。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"radio"` | Yes | フィールドタイプ |
| `label` | `string` | No | グループラベル |
| `options` | `array` | Yes | 選択肢の配列 |
| `default` | `string` | No | デフォルト値 |

### 13. number - 数値入力

数値入力フィールド。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"number"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `min` | `number` | No | 最小値 |
| `max` | `number` | No | 最大値 |
| `step` | `number` | No | ステップ値 |
| `default` | `number` | No | デフォルト値 |

### 14. range - スライダー

スライダーによる数値選択。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"range"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `min` | `number` | No | 最小値（デフォルト: 0） |
| `max` | `number` | No | 最大値（デフォルト: 100） |
| `step` | `number` | No | ステップ値（デフォルト: 1） |
| `showValue` | `boolean` | No | 現在値を表示するか |

### 15. date - 日付選択

日付ピッカー。

```json
{
  "name": "due_date",
  "type": "date",
  "label": "期限",
  "minDate": "today",
  "maxDate": "+30days"
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"date"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `minDate` | `string` | No | 最小日付（`"today"`, `"+Ndays"`, ISO形式） |
| `maxDate` | `string` | No | 最大日付（同上） |

### 16. datetime - 日時選択

日時ピッカー。

```json
{
  "name": "meeting_time",
  "type": "datetime",
  "label": "ミーティング日時"
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"datetime"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |

### 17. file - ファイルアップロード

ファイルアップロードフィールド。

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

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"file"` | Yes | フィールドタイプ |
| `label` | `string` | No | 表示ラベル |
| `accept` | `string` | No | 許可するファイル形式（カンマ区切り） |
| `maxSize` | `number` | No | 最大ファイルサイズ（バイト） |
| `multiple` | `boolean` | No | 複数ファイル許可 |

### 18. hidden - 隠しフィールド

UIに表示されない隠しフィールド。

```json
{
  "name": "form_version",
  "type": "hidden",
  "value": "1.0"
}
```

| プロパティ | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | `string` | Yes | フィールド名 |
| `type` | `"hidden"` | Yes | フィールドタイプ |
| `value` | `any` | Yes | 固定値 |

---

## フロントエンド実装ガイド

### 1. フォームリクエストの検出

SSEストリームからフォームリクエストを検出します。

```typescript
interface FormRequestEvent {
  type: 'tool_use';
  name: 'mcp__form__request_form';
  input: {
    form_schema: FormSchema;
  };
}

interface FormSchema {
  title: string;
  description?: string;
  submitLabel?: string;
  cancelLabel?: string;
  fields: FormField[];
}

// SSEイベントハンドラ
function handleSSEEvent(event: any) {
  if (event.type === 'tool_use' && event.name === 'mcp__form__request_form') {
    const formSchema = event.input.form_schema;
    showFormModal(formSchema);
  }
}
```

### 2. フォームUIの生成

スキーマに基づいてフォームコンポーネントを動的に生成します。

```typescript
// React での実装例
function DynamicForm({ schema, onSubmit, onCancel }: Props) {
  const [formData, setFormData] = useState<Record<string, any>>({});

  const renderField = (field: FormField) => {
    switch (field.type) {
      case 'text':
        return <TextInput field={field} value={formData[field.name]} onChange={handleChange} />;
      case 'select':
        return <SelectInput field={field} value={formData[field.name]} onChange={handleChange} />;
      case 'autocomplete':
        return <AutocompleteInput field={field} value={formData[field.name]} onChange={handleChange} />;
      case 'divider':
        return <Divider label={field.label} />;
      case 'heading':
        return <Heading text={field.text} level={field.level} description={field.description} />;
      // ... 他のフィールドタイプ
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <h2>{schema.title}</h2>
      {schema.description && <p>{schema.description}</p>}

      {schema.fields.map((field, index) => (
        <div key={field.name || index}>
          {renderField(field)}
        </div>
      ))}

      <div className="actions">
        <button type="button" onClick={onCancel}>
          {schema.cancelLabel || 'キャンセル'}
        </button>
        <button type="submit">
          {schema.submitLabel || '送信'}
        </button>
      </div>
    </form>
  );
}
```

### 3. バリデーション実装

```typescript
function validateField(field: FormField, value: any): string | null {
  // 必須チェック
  if (field.required && !value) {
    return `${field.label}は必須です`;
  }

  // 文字数チェック
  if (field.type === 'text' || field.type === 'textarea') {
    if (field.minLength && value.length < field.minLength) {
      return `${field.label}は${field.minLength}文字以上で入力してください`;
    }
    if (field.maxLength && value.length > field.maxLength) {
      return `${field.label}は${field.maxLength}文字以下で入力してください`;
    }
    if (field.pattern && !new RegExp(field.pattern).test(value)) {
      return field.patternError || `${field.label}の形式が正しくありません`;
    }
  }

  // 数値チェック
  if (field.type === 'number' || field.type === 'range') {
    const num = Number(value);
    if (field.min !== undefined && num < field.min) {
      return `${field.label}は${field.min}以上で入力してください`;
    }
    if (field.max !== undefined && num > field.max) {
      return `${field.label}は${field.max}以下で入力してください`;
    }
  }

  // 選択数チェック
  if (field.type === 'multiselect' || field.type === 'multi-autocomplete') {
    const selected = Array.isArray(value) ? value : [];
    if (field.minSelect && selected.length < field.minSelect) {
      return `${field.label}は${field.minSelect}個以上選択してください`;
    }
    if (field.maxSelect && selected.length > field.maxSelect) {
      return `${field.label}は${field.maxSelect}個以下で選択してください`;
    }
  }

  return null;
}
```

### 4. Suggestions の表示

`suggestions`フィールドがある場合、クリック可能な候補として表示します。

```typescript
function SuggestionsComponent({ suggestions, onSelect }: Props) {
  if (!suggestions || suggestions.length === 0) return null;

  return (
    <div className="suggestions">
      <span className="suggestions-label">候補:</span>
      {suggestions.map((suggestion, index) => (
        <button
          key={index}
          type="button"
          className="suggestion-chip"
          onClick={() => onSelect(suggestion)}
        >
          {suggestion.length > 30 ? suggestion.slice(0, 30) + '...' : suggestion}
        </button>
      ))}
    </div>
  );
}
```

### 5. Autocomplete の実装

```typescript
async function fetchAutocompleteOptions(
  field: AutocompleteField,
  query: string
): Promise<any[]> {
  if (query.length < (field.minChars || 1)) {
    return [];
  }

  // パラメータを構築
  const params = new URLSearchParams();
  if (field.searchParams) {
    for (const [key, value] of Object.entries(field.searchParams)) {
      params.set(key, value.replace('{query}', query));
    }
  } else {
    params.set('q', query);
  }

  const response = await fetch(`${field.searchUrl}?${params}`);
  return response.json();
}

function renderOption(field: AutocompleteField, option: any): string {
  if (field.renderTemplate) {
    return field.renderTemplate.replace(
      /\{(\w+)\}/g,
      (_, key) => option[key] || ''
    );
  }
  return option[field.displayField];
}
```

### 6. 送信データの形式

フォームデータはJSON形式で次のメッセージとして送信します。

```typescript
function handleFormSubmit(schema: FormSchema, formData: Record<string, any>) {
  // hiddenフィールドの値を追加
  for (const field of schema.fields) {
    if (field.type === 'hidden') {
      formData[field.name] = field.value;
    }
  }

  // ファイルフィールドは Base64 エンコードまたは参照として送信
  // 実装に応じて調整

  // 次のメッセージとして送信
  sendMessage(JSON.stringify(formData, null, 2));
}
```

### 7. キャンセル処理

```typescript
function handleFormCancel(schema: FormSchema) {
  // キャンセルメッセージを送信
  sendMessage('フォーム入力をキャンセルしました。');
}
```

---

## 使用例

### 例1: プロジェクト作成フォーム

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
      "patternError": "小文字英数字とハイフンのみ使用可能です",
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
        "マイクロサービス間の通信を担うAPIゲートウェイ。認証・認可・レート制限機能付き。"
      ]
    }
  ]
}
```

**送信されるデータ例:**

```json
{
  "project_name": "my-web-app",
  "language": "typescript",
  "features": ["auth", "api"],
  "description": "ユーザー認証機能を持つWebアプリケーション。JWT認証、ソーシャルログイン対応。"
}
```

### 例2: 担当者アサインフォーム

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

AIエージェントがフォームリクエストツールを適切に使用するための Agent Skills 設定例：

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

1. **複数の情報が必要な場合はフォームを使用**
   - プロジェクト作成、設定変更、複雑なデータ入力など

2. **単純な選択のみの場合は通常の会話で確認**
   - はい/いいえ、A/B/Cの選択などはAskUserQuestionツールを使用

3. **検索が必要なフィールドには autocomplete タイプを使用**
   - ユーザー検索、プロジェクト検索など

4. **テキスト入力には suggestions で入力例を提示**
   - ユーザーの入力を助けるヒントを提供

5. **フォームが長くなる場合は divider や heading でセクション分け**
   - 視覚的な整理でユーザビリティを向上

### フォーム例

#### プロジェクト作成
\`\`\`json
{
  "title": "新規プロジェクト作成",
  "fields": [
    {"name": "name", "type": "text", "label": "プロジェクト名", "required": true},
    {"name": "language", "type": "select", "label": "言語", "options": [...]}
  ]
}
\`\`\`
```

---

## エラーハンドリング

### バリデーションエラー

ツール実行時のバリデーションエラーは以下の形式で返されます：

```json
{
  "content": [
    {
      "type": "text",
      "text": "エラー: フィールド[2]（type: text）にnameが指定されていません。"
    }
  ],
  "isError": true
}
```

### 無効なフィールドタイプ

```json
{
  "content": [
    {
      "type": "text",
      "text": "エラー: フィールド[0]の無効なtype: 'invalid_type'。有効なタイプ: async-select, autocomplete, cascading-select, checkbox, date, datetime, divider, file, heading, hidden, multi-autocomplete, multiselect, number, radio, range, select, text, textarea"
    }
  ],
  "isError": true
}
```

---

## 関連ドキュメント

- [ストリーミングAPI仕様](./streaming-specification.md)
- [API仕様書](./api-specification.md)
- [使用ガイド](./usage-guide.md)
