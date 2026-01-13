# ServiceNowドキュメント検索スキル

ServiceNowに関する質問を受けた際に使用するスキルです。

## 使用条件

以下のような質問を受けた場合にこのスキルを使用してください：
- ServiceNowの機能や設定についての質問
- インシデント管理、問題管理、変更管理等のITSM関連
- CMDB（構成管理データベース）について
- ServiceNowのワークフローやスクリプトについて
- ServiceNowのバージョン固有の機能について

## 使用するツール

### 1. searchDocuments（ドキュメント検索）

ServiceNowの公式ドキュメントを検索します。

```
mcp__servicenow-docs__searchDocuments({
  "q": "検索キーワード",
  "labelkey": "yokohama",  // オプション: バージョン指定
  "rpp": 5  // オプション: 結果数
})
```

**パラメータ:**
- `q` (必須): 検索クエリ（例: "incident management", "CMDB", "workflow"）
- `labelkey` (オプション): バージョン指定（yokohama, xanadu, washingtondc等）
- `page` (オプション): ページ番号（デフォルト: 1）
- `rpp` (オプション): 1ページあたりの結果数（デフォルト: 5）

### 2. getDocumentDetail（ドキュメント詳細取得）

検索結果から特定のドキュメントの詳細を取得します。

```
mcp__servicenow-docs__getDocumentDetail({
  "bundleId": "yokohama-it-service-management",
  "pageId": "product/incident-management/concept/c_IncidentManagement.html"
})
```

**パラメータ:**
- `bundleId` (必須): 検索結果に含まれるbundle_id
- `pageId` (必須): 検索結果に含まれるpage_id

## 推奨ワークフロー

1. **まず検索を実行**
   - ユーザーの質問に関連するキーワードで`searchDocuments`を実行
   - 適切な結果が見つからない場合は、キーワードを変えて再検索（同義語、英語/日本語を試す）

2. **関連ドキュメントの詳細を取得**
   - 検索結果から関連性の高いドキュメントを選択
   - `getDocumentDetail`でMarkdown形式の詳細を取得

3. **回答を作成**
   - 取得したドキュメントの内容を基に回答を作成
   - 情報源（ドキュメントのURL）を明記
   - バージョン固有の情報の場合は、バージョンを明記

## 検索のコツ

- 英語キーワードの方が結果が良い場合があります
- 具体的な機能名（例: "Assignment Rules", "Business Rules"）で検索
- バージョン指定で最新の情報を取得（例: "yokohama", "xanadu"）
- 複数の関連キーワードで検索して情報を集約

## 注意事項

- 古いバージョンの情報は最新版と異なる場合があります
- 検索結果が見つからない場合は、表現を変えて再試行してください
- 公式ドキュメント以外の情報（コミュニティ、ブログ等）は含まれません
