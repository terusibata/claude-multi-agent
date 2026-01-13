"""
ServiceNowドキュメント検索ツールの実装
OpenAPI仕様に基づくREST APIをbuiltinツールとしてラップ
"""
import ssl
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ServiceNow Docs Proxy APIのベースURL
SERVICENOW_DOCS_BASE_URL = "https://servicenow-docs-proxy.terusibata.workers.dev"


# ツール定義
SERVICENOW_DOCS_TOOL_DEFINITIONS = {
    "searchDocuments": {
        "name": "searchDocuments",
        "description": "ServiceNowドキュメントを検索します。検索結果にはタイトル、URL、スニペット、バージョン情報が含まれます。良い結果が得られない場合は、キーワードを変えて再検索してください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "検索クエリ文字列（例: 'incident management', 'CMDB', 'workflow'）"
                },
                "labelkey": {
                    "type": "string",
                    "description": "バージョンラベル（yokohama, xanadu, washingtondc等）。省略時は全バージョンから検索"
                },
                "page": {
                    "type": "integer",
                    "description": "ページ番号（1から開始）",
                    "default": 1
                },
                "rpp": {
                    "type": "integer",
                    "description": "1ページあたりの結果数",
                    "default": 5
                }
            },
            "required": ["q"]
        }
    },
    "getDocumentDetail": {
        "name": "getDocumentDetail",
        "description": "ServiceNowドキュメントの詳細をMarkdown形式で取得します。searchDocumentsで取得したbundle_idとpage_idを使用してください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "bundleId": {
                    "type": "string",
                    "description": "バンドルID（例: yokohama-it-service-management）"
                },
                "pageId": {
                    "type": "string",
                    "description": "ページID（例: product/incident-management/concept/c_IncidentManagement.html）"
                }
            },
            "required": ["bundleId", "pageId"]
        }
    }
}


def _create_ssl_context():
    """
    SSL証明書検証を緩和したコンテキストを作成
    （開発環境用、本番環境では適切な証明書管理を推奨）
    """
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


async def _make_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
) -> dict[str, Any]:
    """
    ServiceNow Docs Proxy APIにリクエストを送信

    Args:
        method: HTTPメソッド
        path: APIパス
        params: クエリパラメータ

    Returns:
        APIレスポンス
    """
    url = f"{SERVICENOW_DOCS_BASE_URL}{path}"

    try:
        async with httpx.AsyncClient(
            verify=False,  # SSL証明書検証をスキップ
            timeout=30.0,
        ) as client:
            if method == "GET":
                response = await client.get(url, params=params)
            else:
                response = await client.request(method, url)

            response.raise_for_status()
            return response.json()

    except httpx.HTTPStatusError as e:
        logger.error(
            "ServiceNow Docs API HTTP error",
            status_code=e.response.status_code,
            url=url,
        )
        raise
    except httpx.RequestError as e:
        logger.error(
            "ServiceNow Docs API request error",
            error=str(e),
            url=url,
        )
        raise


def create_search_documents_handler():
    """
    searchDocumentsツールのハンドラーを作成

    Returns:
        ツールハンドラー関数
    """
    async def search_documents_handler(args: dict[str, Any]) -> dict[str, Any]:
        """
        ServiceNowドキュメントを検索

        Args:
            args: ツール引数
                - q: 検索クエリ
                - labelkey: バージョンラベル（オプション）
                - page: ページ番号（オプション）
                - rpp: 結果数（オプション）

        Returns:
            検索結果
        """
        query = args.get("q", "")
        if not query:
            return {
                "content": [{
                    "type": "text",
                    "text": "エラー: 検索クエリ(q)は必須です"
                }],
                "is_error": True
            }

        params = {
            "q": query,
            "page": args.get("page", 1),
            "rpp": args.get("rpp", 5),
        }

        if args.get("labelkey"):
            params["labelkey"] = args["labelkey"]

        try:
            result = await _make_request("GET", "/api/search", params)

            # 結果をフォーマット
            total = result.get("total", 0)
            results = result.get("results", [])

            if not results:
                text = f"検索クエリ「{query}」に一致するドキュメントは見つかりませんでした。\n別のキーワードで検索してみてください。"
            else:
                text = f"## 検索結果: 「{query}」\n\n"
                text += f"**{total}件中 {len(results)}件を表示**\n\n"

                for i, doc in enumerate(results, 1):
                    text += f"### {i}. {doc.get('title', 'タイトルなし')}\n"
                    text += f"- **バージョン**: {doc.get('version', '不明')}\n"
                    text += f"- **bundle_id**: `{doc.get('bundle_id', '')}`\n"
                    text += f"- **page_id**: `{doc.get('page_id', '')}`\n"
                    if doc.get("snippet"):
                        text += f"- **概要**: {doc['snippet']}\n"
                    text += f"- **URL**: {doc.get('url', '')}\n\n"

                text += "\n---\n詳細を確認するには、`getDocumentDetail`ツールでbundle_idとpage_idを指定してください。"

            return {
                "content": [{
                    "type": "text",
                    "text": text
                }],
                "_metadata": {
                    "total": total,
                    "results_count": len(results),
                    "query": query,
                    "results": results
                }
            }

        except Exception as e:
            logger.error("searchDocuments error", error=str(e))
            return {
                "content": [{
                    "type": "text",
                    "text": f"検索中にエラーが発生しました: {str(e)}"
                }],
                "is_error": True
            }

    return search_documents_handler


def create_get_document_detail_handler():
    """
    getDocumentDetailツールのハンドラーを作成

    Returns:
        ツールハンドラー関数
    """
    async def get_document_detail_handler(args: dict[str, Any]) -> dict[str, Any]:
        """
        ServiceNowドキュメントの詳細を取得

        Args:
            args: ツール引数
                - bundleId: バンドルID
                - pageId: ページID

        Returns:
            ドキュメント詳細
        """
        bundle_id = args.get("bundleId", "")
        page_id = args.get("pageId", "")

        if not bundle_id or not page_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "エラー: bundleIdとpageIdは両方必須です"
                }],
                "is_error": True
            }

        try:
            path = f"/api/bundle/{bundle_id}/page/{page_id}"
            result = await _make_request("GET", path)

            # 結果をフォーマット
            title = result.get("title", "タイトルなし")
            bundle_title = result.get("bundle_title", "")
            content = result.get("content", "")
            updated = result.get("updated", "")

            text = f"# {title}\n\n"
            text += f"**バンドル**: {bundle_title}\n"
            text += f"**最終更新**: {updated}\n\n"
            text += "---\n\n"
            text += content

            # 関連ドキュメントがあれば追加
            related = result.get("related", [])
            if related:
                text += "\n\n---\n\n## 関連ドキュメント\n\n"
                for rel in related[:5]:
                    text += f"- **{rel.get('title', '')}**: bundle_id=`{rel.get('bundle_id', '')}`, page_id=`{rel.get('page_id', '')}`\n"

            return {
                "content": [{
                    "type": "text",
                    "text": text
                }],
                "_metadata": {
                    "bundle_id": bundle_id,
                    "page_id": page_id,
                    "title": title,
                    "has_content": bool(content)
                }
            }

        except Exception as e:
            logger.error("getDocumentDetail error", error=str(e), bundle_id=bundle_id, page_id=page_id)
            return {
                "content": [{
                    "type": "text",
                    "text": f"ドキュメント取得中にエラーが発生しました: {str(e)}"
                }],
                "is_error": True
            }

    return get_document_detail_handler


def get_servicenow_docs_tool_definition(tool_name: str) -> dict[str, Any] | None:
    """
    ServiceNowドキュメントツールの定義を取得

    Args:
        tool_name: ツール名

    Returns:
        ツール定義（存在しない場合はNone）
    """
    return SERVICENOW_DOCS_TOOL_DEFINITIONS.get(tool_name)


def get_all_servicenow_docs_tool_definitions() -> list[dict[str, Any]]:
    """
    全ServiceNowドキュメントツールの定義を取得

    Returns:
        ツール定義のリスト
    """
    return list(SERVICENOW_DOCS_TOOL_DEFINITIONS.values())


# ツールハンドラーのマッピング
SERVICENOW_DOCS_TOOL_HANDLERS = {
    "searchDocuments": create_search_documents_handler,
    "getDocumentDetail": create_get_document_detail_handler,
}


def get_servicenow_docs_tool_handler(tool_name: str):
    """
    ServiceNowドキュメントツールのハンドラーを取得

    Args:
        tool_name: ツール名

    Returns:
        ハンドラー作成関数（存在しない場合はNone）
    """
    return SERVICENOW_DOCS_TOOL_HANDLERS.get(tool_name)


def create_servicenow_docs_mcp_server():
    """
    ServiceNowドキュメント検索用のSDK MCPサーバーを作成

    Returns:
        SDK MCPサーバー設定（SDKが利用できない場合はNone）
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping ServiceNow docs MCP server")
        return None

    # searchDocumentsツールを定義
    @tool(
        "searchDocuments",
        "ServiceNowドキュメントを検索します。検索結果にはタイトル、URL、スニペット、バージョン情報が含まれます。良い結果が得られない場合は、キーワードを変えて再検索してください。",
        {
            "q": str,
            "labelkey": Optional[str],
            "page": Optional[int],
            "rpp": Optional[int],
        }
    )
    async def search_documents_tool(args: dict[str, Any]) -> dict[str, Any]:
        """
        ServiceNowドキュメントを検索
        """
        handler = create_search_documents_handler()
        return await handler(args)

    # getDocumentDetailツールを定義
    @tool(
        "getDocumentDetail",
        "ServiceNowドキュメントの詳細をMarkdown形式で取得します。searchDocumentsで取得したbundle_idとpage_idを使用してください。",
        {
            "bundleId": str,
            "pageId": str,
        }
    )
    async def get_document_detail_tool(args: dict[str, Any]) -> dict[str, Any]:
        """
        ServiceNowドキュメントの詳細を取得
        """
        handler = create_get_document_detail_handler()
        return await handler(args)

    # MCPサーバーとして登録
    server = create_sdk_mcp_server(
        name="servicenow-docs",
        version="1.0.0",
        tools=[search_documents_tool, get_document_detail_tool]
    )

    return server


# ServiceNowドキュメント関連のシステムプロンプト
SERVICENOW_DOCS_PROMPT = """
## ServiceNowドキュメント検索について

ServiceNowに関する質問を受けた場合は、`mcp__servicenow-docs__searchDocuments`ツールを使用してドキュメントを検索してください。

### searchDocumentsツールの使用方法
- q: 検索クエリ（例: "incident management", "CMDB", "workflow"）
- labelkey: バージョン指定（yokohama, xanadu, washingtondc等）。省略時は全バージョンから検索
- page: ページ番号（デフォルト: 1）
- rpp: 1ページあたりの結果数（デフォルト: 5）

### getDocumentDetailツールの使用方法
検索結果で詳細を確認したいドキュメントがあれば、bundle_idとpage_idを指定して詳細を取得:
- bundleId: 検索結果のbundle_id
- pageId: 検索結果のpage_id

### 推奨ワークフロー
1. まず`searchDocuments`で関連ドキュメントを検索
2. 適切な結果が見つからない場合は、キーワードを変えて再検索
3. 関連するドキュメントが見つかったら`getDocumentDetail`で詳細を取得
4. 取得した情報を基に回答を作成
"""
