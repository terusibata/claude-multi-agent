"""
モデル管理APIのテスト
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_model(client: AsyncClient):
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


@pytest.mark.asyncio
async def test_get_models(client: AsyncClient):
    """モデル一覧取得テスト"""
    # まずモデルを作成
    model_data = {
        "model_id": "test-model-list",
        "display_name": "Test Model",
        "bedrock_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "input_token_price": "0.8",
        "output_token_price": "4.0",
    }
    await client.post("/api/models", json=model_data)

    # 一覧取得
    response = await client.get("/api/models")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_update_model_status(client: AsyncClient):
    """モデルステータス更新テスト"""
    # まずモデルを作成
    model_data = {
        "model_id": "test-model-status",
        "display_name": "Status Test Model",
        "bedrock_model_id": "us.anthropic.claude-test-v1:0",
        "input_token_price": "1.0",
        "output_token_price": "5.0",
    }
    await client.post("/api/models", json=model_data)

    # ステータスを更新
    response = await client.patch(
        "/api/models/test-model-status/status?status=deprecated"
    )
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "deprecated"
