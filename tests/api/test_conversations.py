"""
会話管理APIのテスト
"""
import pytest
from httpx import AsyncClient


class TestConversationsCRUD:
    """会話CRUD操作のテスト"""

    @pytest.mark.integration
    async def test_create_conversation(
        self, client: AsyncClient, sample_tenant: dict, sample_model: dict
    ):
        """会話作成テスト"""
        conversation_data = {
            "user_id": "user-001",
            "model_id": sample_model["model_id"],
            "enable_workspace": False,
        }

        response = await client.post(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations",
            json=conversation_data,
        )
        assert response.status_code == 201

        data = response.json()
        assert data["tenant_id"] == sample_tenant["tenant_id"]
        assert data["user_id"] == "user-001"
        assert data["model_id"] == sample_model["model_id"]
        assert data["status"] == "active"
        assert data["enable_workspace"] is False
        assert "conversation_id" in data

    @pytest.mark.integration
    async def test_create_conversation_with_workspace(
        self, client: AsyncClient, sample_tenant: dict, sample_model: dict
    ):
        """ワークスペース有効で会話作成"""
        conversation_data = {
            "user_id": "user-002",
            "model_id": sample_model["model_id"],
            "enable_workspace": True,
        }

        response = await client.post(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations",
            json=conversation_data,
        )
        assert response.status_code == 201

        data = response.json()
        assert data["enable_workspace"] is True

    @pytest.mark.integration
    async def test_create_conversation_uses_tenant_default_model(
        self, client: AsyncClient, sample_tenant: dict
    ):
        """model_id省略時はテナントデフォルトモデルを使用"""
        conversation_data = {
            "user_id": "user-003",
            # model_id省略
        }

        response = await client.post(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations",
            json=conversation_data,
        )
        assert response.status_code == 201

        data = response.json()
        # テナントのデフォルトモデルが設定される
        assert data["model_id"] == sample_tenant["model_id"]

    @pytest.mark.integration
    async def test_create_conversation_tenant_not_found(self, client: AsyncClient):
        """存在しないテナントで会話作成"""
        conversation_data = {
            "user_id": "user-001",
        }

        response = await client.post(
            "/api/tenants/non-existent-tenant/conversations",
            json=conversation_data,
        )
        assert response.status_code == 404

    @pytest.mark.integration
    async def test_get_conversations(
        self, client: AsyncClient, sample_tenant: dict, sample_conversation: dict
    ):
        """会話一覧取得テスト"""
        response = await client.get(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations"
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        conversation_ids = [c["conversation_id"] for c in data]
        assert sample_conversation["conversation_id"] in conversation_ids

    @pytest.mark.integration
    async def test_get_conversations_filter_by_user(
        self, client: AsyncClient, sample_tenant: dict, sample_model: dict
    ):
        """ユーザーでフィルタリング"""
        # 特定ユーザーの会話を作成
        conversation_data = {
            "user_id": "specific-user",
            "model_id": sample_model["model_id"],
        }
        await client.post(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations",
            json=conversation_data,
        )

        # フィルタリング
        response = await client.get(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations?user_id=specific-user"
        )
        assert response.status_code == 200

        data = response.json()
        assert all(c["user_id"] == "specific-user" for c in data)

    @pytest.mark.integration
    async def test_get_conversations_filter_by_status(
        self, client: AsyncClient, sample_tenant: dict, sample_conversation: dict
    ):
        """ステータスでフィルタリング"""
        response = await client.get(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations?status=active"
        )
        assert response.status_code == 200

        data = response.json()
        assert all(c["status"] == "active" for c in data)

    @pytest.mark.integration
    async def test_get_conversation_by_id(
        self, client: AsyncClient, sample_tenant: dict, sample_conversation: dict
    ):
        """特定会話取得テスト"""
        response = await client.get(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations/{sample_conversation['conversation_id']}"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["conversation_id"] == sample_conversation["conversation_id"]

    @pytest.mark.integration
    async def test_get_conversation_not_found(
        self, client: AsyncClient, sample_tenant: dict
    ):
        """存在しない会話で404エラー"""
        response = await client.get(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404

    @pytest.mark.integration
    async def test_get_conversation_wrong_tenant(
        self, client: AsyncClient, sample_conversation: dict, sample_model: dict
    ):
        """別テナントの会話にアクセス不可"""
        # 別のテナントを作成
        other_tenant_data = {"tenant_id": "other-tenant"}
        await client.post("/api/tenants", json=other_tenant_data)

        # 別テナントから会話にアクセス
        response = await client.get(
            f"/api/tenants/other-tenant/conversations/{sample_conversation['conversation_id']}"
        )
        assert response.status_code == 404

    @pytest.mark.integration
    async def test_update_conversation(
        self, client: AsyncClient, sample_tenant: dict, sample_conversation: dict
    ):
        """会話更新テスト"""
        update_data = {
            "title": "Updated Conversation Title",
        }

        response = await client.put(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations/{sample_conversation['conversation_id']}",
            json=update_data,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["title"] == "Updated Conversation Title"

    @pytest.mark.integration
    async def test_archive_conversation(
        self, client: AsyncClient, sample_tenant: dict, sample_conversation: dict
    ):
        """会話アーカイブテスト"""
        update_data = {
            "status": "archived",
        }

        response = await client.put(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations/{sample_conversation['conversation_id']}",
            json=update_data,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "archived"

    @pytest.mark.integration
    async def test_delete_conversation(
        self, client: AsyncClient, sample_tenant: dict, sample_model: dict
    ):
        """会話削除テスト"""
        # 削除用会話を作成
        conversation_data = {
            "user_id": "delete-test-user",
            "model_id": sample_model["model_id"],
        }
        create_response = await client.post(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations",
            json=conversation_data,
        )
        conversation_id = create_response.json()["conversation_id"]

        # 削除
        delete_response = await client.delete(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations/{conversation_id}"
        )
        assert delete_response.status_code == 204

        # 削除確認
        get_response = await client.get(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations/{conversation_id}"
        )
        assert get_response.status_code == 404


class TestConversationMessages:
    """会話メッセージ関連のテスト"""

    @pytest.mark.integration
    async def test_get_messages_empty(
        self, client: AsyncClient, sample_tenant: dict, sample_conversation: dict
    ):
        """メッセージ一覧取得（空）"""
        response = await client.get(
            f"/api/tenants/{sample_tenant['tenant_id']}/conversations/{sample_conversation['conversation_id']}/messages"
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0  # 新規会話なのでメッセージなし
