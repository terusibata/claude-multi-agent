"""
テスト用共通設定
pytest fixtures with testcontainers
"""
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.database import Base, get_db
from app.main import app


# PostgreSQLコンテナ（セッション単位で共有）
@pytest.fixture(scope="session")
def postgres_container():
    """PostgreSQLコンテナを起動"""
    with PostgresContainer(
        image="postgres:15-alpine",
        username="test",
        password="test",
        dbname="testdb",
    ) as postgres:
        yield postgres


@pytest.fixture(scope="session")
def database_url(postgres_container) -> str:
    """非同期用データベースURL"""
    # testcontainersはpsycopg2用のURLを返すので、asyncpg用に変換
    url = postgres_container.get_connection_url()
    # postgresql://... -> postgresql+asyncpg://...
    return url.replace("postgresql://", "postgresql+asyncpg://").replace(
        "psycopg2", "asyncpg"
    )


@pytest.fixture(scope="session")
def sync_database_url(postgres_container) -> str:
    """同期用データベースURL（factory_boy用）"""
    return postgres_container.get_connection_url()


@pytest_asyncio.fixture(scope="function")
async def engine(database_url: str):
    """非同期エンジン"""
    engine = create_async_engine(database_url, echo=False)

    # テーブル作成
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # テーブル削除（クリーンアップ）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """テスト用データベースセッション"""
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session
        # ロールバック（テスト間の独立性を保証）
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """テスト用HTTPクライアント"""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# =============================================================================
# 共通テストデータ fixtures
# =============================================================================


@pytest_asyncio.fixture
async def sample_model(client: AsyncClient) -> dict:
    """サンプルモデルを作成"""
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
    return response.json()


@pytest_asyncio.fixture
async def sample_tenant(client: AsyncClient, sample_model: dict) -> dict:
    """サンプルテナントを作成"""
    tenant_data = {
        "tenant_id": "test-tenant-001",
        "system_prompt": "You are a helpful assistant.",
        "model_id": sample_model["model_id"],
    }
    response = await client.post("/api/tenants", json=tenant_data)
    assert response.status_code == 201
    return response.json()


@pytest_asyncio.fixture
async def sample_conversation(
    client: AsyncClient, sample_tenant: dict, sample_model: dict
) -> dict:
    """サンプル会話を作成"""
    conversation_data = {
        "user_id": "test-user-001",
        "model_id": sample_model["model_id"],
        "workspace_enabled": False,
    }
    response = await client.post(
        f"/api/tenants/{sample_tenant['tenant_id']}/conversations",
        json=conversation_data,
    )
    assert response.status_code == 201
    return response.json()


# =============================================================================
# S3モック fixtures（moto使用）
# =============================================================================


@pytest.fixture
def aws_credentials():
    """AWS認証情報をモック用に設定"""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def s3_bucket_name():
    """テスト用S3バケット名"""
    return "test-workspace-bucket"
