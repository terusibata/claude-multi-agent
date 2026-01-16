---
name: servicenow-docs-search
description: Search and retrieve ServiceNow documentation. Use when answering questions about ServiceNow features, configuration, ITSM processes, CMDB, workflows, or any ServiceNow-related topics.
allowed-tools: mcp__servicenow-docs__searchDocuments, mcp__servicenow-docs__getDocumentDetail
user-invocable: true
---

# ServiceNow Documentation Search

ServiceNowに関する質問に回答するためのスキルです。

## 使用条件

以下のような質問を受けた場合にこのスキルを使用してください：
- ServiceNowの機能や設定についての質問
- インシデント管理、問題管理、変更管理等のITSM関連
- CMDB（構成管理データベース）について
- ServiceNowのワークフローやスクリプトについて
- ServiceNowのバージョン固有の機能について

## ツールの使用方法

### Step 1: ドキュメント検索

まず `mcp__servicenow-docs__searchDocuments` で関連ドキュメントを検索します。

```json
{
  "q": "検索キーワード",
  "labelkey": "yokohama",
  "rpp": 5
}
```

**パラメータ:**
- `q` (必須): 検索クエリ（例: "incident management", "CMDB", "workflow"）
- `labelkey` (オプション): バージョン指定（yokohama, xanadu, washingtondc等）
- `rpp` (オプション): 結果数（デフォルト: 5）

### Step 2: 詳細取得

検索結果から関連するドキュメントの `bundle_id` と `page_id` を使用して詳細を取得します。

```json
{
  "bundleId": "yokohama-it-service-management",
  "pageId": "product/incident-management/concept/c_IncidentManagement.html"
}
```

## 推奨ワークフロー

1. ユーザーの質問から適切な検索キーワードを抽出
2. `searchDocuments` で検索（英語キーワードの方が結果が良い場合あり）
3. 適切な結果がない場合は、同義語や別の表現で再検索
4. 関連ドキュメントが見つかったら `getDocumentDetail` で詳細取得
5. 取得した情報を基に回答を作成（情報源のURLを明記）

## 検索のコツ

- 具体的な機能名で検索（例: "Assignment Rules", "Business Rules"）
- 最新バージョンの情報が必要な場合は `labelkey` を指定
- 検索結果が少ない場合は、より一般的な用語で再検索
