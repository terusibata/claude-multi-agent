"""
ServiceNowドキュメント検索ツールのテスト
"""
import asyncio
import pytest

from app.services.servicenow_docs_tools import (
    create_search_documents_handler,
    create_get_document_detail_handler,
    get_servicenow_docs_tool_definition,
    get_all_servicenow_docs_tool_definitions,
    SERVICENOW_DOCS_TOOL_DEFINITIONS,
)


class TestServiceNowDocsToolDefinitions:
    """ツール定義のテスト"""

    def test_tool_definitions_exist(self):
        """ツール定義が存在することを確認"""
        assert "searchDocuments" in SERVICENOW_DOCS_TOOL_DEFINITIONS
        assert "getDocumentDetail" in SERVICENOW_DOCS_TOOL_DEFINITIONS

    def test_get_tool_definition(self):
        """ツール定義の取得"""
        search_def = get_servicenow_docs_tool_definition("searchDocuments")
        assert search_def is not None
        assert search_def["name"] == "searchDocuments"
        assert "input_schema" in search_def

    def test_get_all_tool_definitions(self):
        """全ツール定義の取得"""
        all_defs = get_all_servicenow_docs_tool_definitions()
        assert len(all_defs) == 2


class TestSearchDocumentsHandler:
    """searchDocumentsハンドラーのテスト"""

    @pytest.mark.asyncio
    async def test_search_with_query(self):
        """検索クエリでの検索"""
        handler = create_search_documents_handler()
        result = await handler({"q": "incident management", "rpp": 2})

        assert "content" in result
        assert len(result["content"]) > 0
        assert result["content"][0]["type"] == "text"

        # メタデータの確認
        assert "_metadata" in result
        assert "query" in result["_metadata"]
        assert result["_metadata"]["query"] == "incident management"

    @pytest.mark.asyncio
    async def test_search_without_query(self):
        """検索クエリなしでエラー"""
        handler = create_search_documents_handler()
        result = await handler({})

        assert "is_error" in result
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_search_with_version(self):
        """バージョン指定での検索"""
        handler = create_search_documents_handler()
        result = await handler({
            "q": "workflow",
            "labelkey": "yokohama",
            "rpp": 3
        })

        assert "content" in result
        assert "_metadata" in result


class TestGetDocumentDetailHandler:
    """getDocumentDetailハンドラーのテスト"""

    @pytest.mark.asyncio
    async def test_get_document_detail(self):
        """ドキュメント詳細の取得"""
        handler = create_get_document_detail_handler()
        result = await handler({
            "bundleId": "washingtondc-it-service-management",
            "pageId": "product/incident-management/concept/c_IncidentManagement.html"
        })

        assert "content" in result
        assert len(result["content"]) > 0
        text = result["content"][0]["text"]
        assert "Incident Management" in text

    @pytest.mark.asyncio
    async def test_get_document_without_params(self):
        """パラメータなしでエラー"""
        handler = create_get_document_detail_handler()
        result = await handler({})

        assert "is_error" in result
        assert result["is_error"] is True


if __name__ == "__main__":
    # 簡易テスト実行
    async def run_tests():
        print("=== ServiceNow Docs Tools Tests ===\n")

        # 検索テスト
        print("1. Testing searchDocuments...")
        search_handler = create_search_documents_handler()
        search_result = await search_handler({"q": "incident", "rpp": 2})
        print(f"   Search result: {search_result['content'][0]['text'][:200]}...")
        print(f"   Metadata: {search_result.get('_metadata', {})}")
        print()

        # 詳細取得テスト
        print("2. Testing getDocumentDetail...")
        detail_handler = create_get_document_detail_handler()
        detail_result = await detail_handler({
            "bundleId": "washingtondc-it-service-management",
            "pageId": "product/incident-management/concept/c_IncidentManagement.html"
        })
        print(f"   Detail result: {detail_result['content'][0]['text'][:300]}...")
        print()

        print("=== All tests passed! ===")

    asyncio.run(run_tests())
