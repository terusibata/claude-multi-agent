"""
テナント管理APIのテスト
"""
import pytest
from httpx import AsyncClient


class TestTenantsCRUD:
    """テナントCRUD操作のテスト"""

    @pytest.mark.integration
    async def test_create_tenant(self, client: AsyncClient, sample_model: dict):
        """テナント作成テスト"""
        tenant_data = {
            "tenant_id": "new-tenant-001",
            "system_prompt": "You are a helpful assistant.",
            "model_id": sample_model["model_id"],
        }

        response = await client.post("/api/tenants", json=tenant_data)
        assert response.status_code == 201

        data = response.json()
        assert data["tenant_id"] == "new-tenant-001"
        assert data["system_prompt"] == "You are a helpful assistant."
        assert data["model_id"] == sample_model["model_id"]
        assert data["status"] == "active"

    @pytest.mark.integration
    async def test_create_tenant_minimal(self, client: AsyncClient):
        """最小限のテナント作成（model_id, system_promptなし）"""
        tenant_data = {
            "tenant_id": "minimal-tenant",
        }

        response = await client.post("/api/tenants", json=tenant_data)
        assert response.status_code == 201

        data = response.json()
        assert data["tenant_id"] == "minimal-tenant"
        assert data["system_prompt"] is None
        assert data["model_id"] is None

    @pytest.mark.integration
    async def test_create_tenant_duplicate(self, client: AsyncClient, sample_tenant: dict):
        """重複テナント作成で409エラー"""
        tenant_data = {
            "tenant_id": sample_tenant["tenant_id"],  # 既存のtenant_id
        }

        response = await client.post("/api/tenants", json=tenant_data)
        assert response.status_code == 409

    @pytest.mark.integration
    async def test_create_tenant_invalid_model(self, client: AsyncClient):
        """存在しないモデルでテナント作成"""
        tenant_data = {
            "tenant_id": "invalid-model-tenant",
            "model_id": "non-existent-model",
        }

        response = await client.post("/api/tenants", json=tenant_data)
        # 外部キー制約違反で500または400
        assert response.status_code in [400, 500]

    @pytest.mark.integration
    async def test_get_tenants(self, client: AsyncClient, sample_tenant: dict):
        """テナント一覧取得テスト"""
        response = await client.get("/api/tenants")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        tenant_ids = [t["tenant_id"] for t in data]
        assert sample_tenant["tenant_id"] in tenant_ids

    @pytest.mark.integration
    async def test_get_tenant_by_id(self, client: AsyncClient, sample_tenant: dict):
        """特定テナント取得テスト"""
        response = await client.get(f"/api/tenants/{sample_tenant['tenant_id']}")
        assert response.status_code == 200

        data = response.json()
        assert data["tenant_id"] == sample_tenant["tenant_id"]

    @pytest.mark.integration
    async def test_get_tenant_not_found(self, client: AsyncClient):
        """存在しないテナントで404エラー"""
        response = await client.get("/api/tenants/non-existent-tenant")
        assert response.status_code == 404

    @pytest.mark.integration
    async def test_update_tenant(self, client: AsyncClient, sample_tenant: dict):
        """テナント更新テスト"""
        update_data = {
            "system_prompt": "Updated system prompt for testing.",
        }

        response = await client.put(
            f"/api/tenants/{sample_tenant['tenant_id']}", json=update_data
        )
        assert response.status_code == 200

        data = response.json()
        assert data["system_prompt"] == "Updated system prompt for testing."

    @pytest.mark.integration
    async def test_update_tenant_status(self, client: AsyncClient, sample_tenant: dict):
        """テナントステータス更新テスト"""
        update_data = {
            "status": "inactive",
        }

        response = await client.put(
            f"/api/tenants/{sample_tenant['tenant_id']}", json=update_data
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "inactive"

    @pytest.mark.integration
    async def test_delete_tenant(self, client: AsyncClient, sample_model: dict):
        """テナント削除テスト"""
        # 削除用テナントを作成
        tenant_data = {
            "tenant_id": "delete-test-tenant",
            "model_id": sample_model["model_id"],
        }
        create_response = await client.post("/api/tenants", json=tenant_data)
        assert create_response.status_code == 201

        # 削除
        delete_response = await client.delete("/api/tenants/delete-test-tenant")
        assert delete_response.status_code == 204

        # 削除確認
        get_response = await client.get("/api/tenants/delete-test-tenant")
        assert get_response.status_code == 404

    @pytest.mark.integration
    async def test_delete_tenant_not_found(self, client: AsyncClient):
        """存在しないテナントの削除で404エラー"""
        response = await client.delete("/api/tenants/non-existent-tenant")
        assert response.status_code == 404
