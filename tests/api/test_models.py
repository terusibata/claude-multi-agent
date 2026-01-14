"""
モデル管理APIのテスト
"""
import pytest
from httpx import AsyncClient


class TestModelsCRUD:
    """モデルCRUD操作のテスト"""

    @pytest.mark.integration
    async def test_create_model(self, client: AsyncClient):
        """モデル作成テスト"""
        model_data = {
            "model_id": "test-claude-sonnet",
            "display_name": "Claude Sonnet (Test)",
            "bedrock_model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "model_region": "us-west-2",
            "input_token_price": "3.0",
            "output_token_price": "15.0",
            "cache_creation_price": "3.75",
            "cache_read_price": "0.30",
        }

        response = await client.post("/api/models", json=model_data)
        assert response.status_code == 201

        data = response.json()
        assert data["model_id"] == "test-claude-sonnet"
        assert data["display_name"] == "Claude Sonnet (Test)"
        assert data["status"] == "active"

    @pytest.mark.integration
    async def test_create_model_duplicate(self, client: AsyncClient, sample_model: dict):
        """重複モデル作成で409エラー"""
        model_data = {
            "model_id": sample_model["model_id"],  # 既存のmodel_id
            "display_name": "Duplicate Model",
            "bedrock_model_id": "some-bedrock-id",
            "input_token_price": "1.0",
            "output_token_price": "5.0",
        }

        response = await client.post("/api/models", json=model_data)
        assert response.status_code == 409

    @pytest.mark.integration
    async def test_get_models(self, client: AsyncClient, sample_model: dict):
        """モデル一覧取得テスト"""
        response = await client.get("/api/models")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        # sample_modelが含まれている
        model_ids = [m["model_id"] for m in data]
        assert sample_model["model_id"] in model_ids

    @pytest.mark.integration
    async def test_get_models_filter_by_status(self, client: AsyncClient, sample_model: dict):
        """ステータスでフィルタリング"""
        response = await client.get("/api/models?status=active")
        assert response.status_code == 200

        data = response.json()
        assert all(m["status"] == "active" for m in data)

    @pytest.mark.integration
    async def test_get_model_by_id(self, client: AsyncClient, sample_model: dict):
        """特定モデル取得テスト"""
        response = await client.get(f"/api/models/{sample_model['model_id']}")
        assert response.status_code == 200

        data = response.json()
        assert data["model_id"] == sample_model["model_id"]

    @pytest.mark.integration
    async def test_get_model_not_found(self, client: AsyncClient):
        """存在しないモデルで404エラー"""
        response = await client.get("/api/models/non-existent-model")
        assert response.status_code == 404

    @pytest.mark.integration
    async def test_update_model(self, client: AsyncClient, sample_model: dict):
        """モデル更新テスト"""
        update_data = {
            "display_name": "Updated Display Name",
            "input_token_price": "5.0",
        }

        response = await client.put(
            f"/api/models/{sample_model['model_id']}", json=update_data
        )
        assert response.status_code == 200

        data = response.json()
        assert data["display_name"] == "Updated Display Name"
        assert data["input_token_price"] == "5.000000"

    @pytest.mark.integration
    async def test_update_model_status(self, client: AsyncClient, sample_model: dict):
        """モデルステータス更新テスト"""
        response = await client.patch(
            f"/api/models/{sample_model['model_id']}/status?status=deprecated"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "deprecated"


class TestModelDelete:
    """モデル削除のテスト（紐づきチェック）"""

    @pytest.mark.integration
    async def test_delete_model_success(self, client: AsyncClient):
        """紐づきのないモデルを削除"""
        # 新しいモデルを作成（どこからも参照されない）
        model_data = {
            "model_id": "test-delete-model",
            "display_name": "Delete Test Model",
            "bedrock_model_id": "us.anthropic.claude-test-v1:0",
            "input_token_price": "1.0",
            "output_token_price": "5.0",
        }
        create_response = await client.post("/api/models", json=model_data)
        assert create_response.status_code == 201

        # 削除
        delete_response = await client.delete("/api/models/test-delete-model")
        assert delete_response.status_code == 204

        # 削除確認
        get_response = await client.get("/api/models/test-delete-model")
        assert get_response.status_code == 404

    @pytest.mark.integration
    async def test_delete_model_in_use_by_tenant(
        self, client: AsyncClient, sample_tenant: dict, sample_model: dict
    ):
        """テナントで使用中のモデルは削除不可"""
        # sample_tenantはsample_modelを使用している
        response = await client.delete(f"/api/models/{sample_model['model_id']}")
        assert response.status_code == 409

        data = response.json()
        assert "usage" in data["detail"]
        assert data["detail"]["usage"]["tenants"] >= 1

    @pytest.mark.integration
    async def test_delete_model_in_use_by_conversation(
        self, client: AsyncClient, sample_conversation: dict, sample_model: dict
    ):
        """会話で使用中のモデルは削除不可"""
        # sample_conversationはsample_modelを使用している
        response = await client.delete(f"/api/models/{sample_model['model_id']}")
        assert response.status_code == 409

        data = response.json()
        assert "usage" in data["detail"]
        assert data["detail"]["usage"]["conversations"] >= 1

    @pytest.mark.integration
    async def test_delete_model_not_found(self, client: AsyncClient):
        """存在しないモデルの削除で404エラー"""
        response = await client.delete("/api/models/non-existent-model")
        assert response.status_code == 404
