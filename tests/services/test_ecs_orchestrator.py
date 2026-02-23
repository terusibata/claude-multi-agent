"""
ECS モード Orchestrator ユニットテスト

ContainerOrchestrator が ECS コンテナ情報を正しく処理するかをテストする:
  - _make_agent_client が ECS/Docker で異なるクライアントを返す
  - get_container_info が Redis 読み取りのみで副作用なし
  - update_mcp_header_rules が ECS モードで HTTP POST を使う
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.container.models import ContainerInfo, ContainerStatus


def _make_container_info(**overrides):
    defaults = {
        "id": "ws-ecs-001",
        "conversation_id": "conv-001",
        "agent_socket": "http://10.0.1.5:9000",
        "proxy_socket": "",
        "status": ContainerStatus.READY,
        "manager_type": "ecs",
        "task_arn": "arn:aws:ecs:us-west-2:123:task/cluster/abc",
        "task_ip": "10.0.1.5",
    }
    defaults.update(overrides)
    return ContainerInfo(**defaults)


def _make_docker_container_info(**overrides):
    defaults = {
        "id": "ws-docker-001",
        "conversation_id": "conv-002",
        "agent_socket": "/var/run/workspace-sockets/ws-docker-001/agent.sock",
        "proxy_socket": "/var/run/workspace-sockets/ws-docker-001/proxy.sock",
        "status": ContainerStatus.READY,
        "manager_type": "docker",
    }
    defaults.update(overrides)
    return ContainerInfo(**defaults)


def _make_orchestrator():
    from app.services.container.orchestrator import ContainerOrchestrator

    mock_lifecycle = AsyncMock()
    mock_lifecycle.is_healthy = AsyncMock(return_value=True)
    mock_lifecycle.destroy_container = AsyncMock()
    mock_lifecycle.list_workspace_containers = AsyncMock(return_value=[])

    mock_warm_pool = AsyncMock()
    mock_warm_pool.acquire = AsyncMock()
    mock_warm_pool.drain = AsyncMock()

    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.hset = AsyncMock()
    mock_redis.expire = AsyncMock()
    mock_redis.delete = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.exists = AsyncMock(return_value=True)
    mock_redis.incr = AsyncMock(return_value=1)

    orchestrator = ContainerOrchestrator(mock_lifecycle, mock_warm_pool, mock_redis)
    return orchestrator, mock_lifecycle, mock_warm_pool, mock_redis


class TestMakeAgentClient:
    """_make_agent_client のデュアルモードテスト"""

    def test_ecs_mode_returns_tcp_client(self):
        """ECS モードでは TCP httpx クライアントを返す"""
        orchestrator, _, _, _ = _make_orchestrator()
        info = _make_container_info()

        client, base_url = orchestrator._make_agent_client(info)

        assert base_url == "http://10.0.1.5:9000"
        # ECS: transport は未指定（デフォルトのTCP）
        assert isinstance(client, httpx.AsyncClient)

    def test_docker_mode_returns_uds_client(self):
        """Docker モードでは UDS httpx クライアントを返す"""
        orchestrator, _, _, _ = _make_orchestrator()
        info = _make_docker_container_info()

        client, base_url = orchestrator._make_agent_client(info)

        assert base_url == "http://localhost"
        assert isinstance(client, httpx.AsyncClient)


class TestGetContainerInfo:
    """get_container_info メソッドのテスト"""

    @pytest.mark.asyncio
    async def test_returns_info_from_redis(self):
        """Redis にコンテナ情報がある場合、それを返す"""
        orchestrator, _, _, mock_redis = _make_orchestrator()
        info = _make_container_info()
        mock_redis.hgetall.return_value = info.to_redis_hash()

        result = await orchestrator.get_container_info("conv-001")

        assert result is not None
        assert result.id == "ws-ecs-001"
        assert result.manager_type == "ecs"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        """Redis にコンテナ情報がない場合、None を返す"""
        orchestrator, _, _, mock_redis = _make_orchestrator()
        mock_redis.hgetall.return_value = {}

        result = await orchestrator.get_container_info("conv-missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_does_not_create_container(self):
        """get_container_info は新規コンテナを作成しない"""
        orchestrator, _, mock_warm_pool, mock_redis = _make_orchestrator()
        mock_redis.hgetall.return_value = {}

        await orchestrator.get_container_info("conv-missing")

        # WarmPool.acquire は呼ばれない
        mock_warm_pool.acquire.assert_not_called()


class TestStopProxy:
    """stop_proxy のpublic API テスト"""

    @pytest.mark.asyncio
    async def test_stop_proxy_is_public(self):
        """stop_proxy がpublicメソッドとしてアクセス可能"""
        orchestrator, _, _, _ = _make_orchestrator()
        # メソッドが存在し、呼び出し可能であること
        assert callable(orchestrator.stop_proxy)
        # ECSモードでは proxy 辞書にエントリがないので no-op
        await orchestrator.stop_proxy("ws-ecs-001")

    @pytest.mark.asyncio
    async def test_stop_proxy_removes_docker_proxy(self):
        """Docker proxy が登録されている場合、stop で削除される"""
        orchestrator, _, _, _ = _make_orchestrator()

        mock_proxy = AsyncMock()
        orchestrator._proxies["ws-docker-001"] = mock_proxy

        await orchestrator.stop_proxy("ws-docker-001")

        mock_proxy.stop.assert_called_once()
        assert "ws-docker-001" not in orchestrator._proxies


class TestUpdateMcpHeaderRules:
    """ECS モードでの MCP ルール更新テスト"""

    @pytest.mark.asyncio
    async def test_ecs_mode_uses_http_post(self):
        """ECS モードで HTTP POST を使ってルールを更新する"""
        orchestrator, _, _, mock_redis = _make_orchestrator()
        orchestrator._settings.container_manager_type = "ecs"
        orchestrator._settings.ecs_proxy_admin_port = 8081

        info = _make_container_info()
        mock_redis.get.return_value = "conv-001"
        mock_redis.hgetall.return_value = info.to_redis_hash()

        # admin HTTPクライアントをモック
        mock_http_resp = MagicMock()
        mock_http_resp.status_code = 200
        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_http_resp)
        orchestrator._admin_http_client = mock_http_client

        from app.services.proxy.credential_proxy import McpHeaderRule

        rules = {
            "test-server": McpHeaderRule(
                real_base_url="https://api.example.com",
                headers={"Authorization": "Bearer token123"},
            )
        }

        await orchestrator.update_mcp_header_rules("ws-ecs-001", rules)

        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        assert "8081" in call_args[0][0]
        assert "admin/update-rules" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_docker_mode_uses_local_proxy(self):
        """Docker モードではローカル Proxy インスタンスに直接設定する"""
        orchestrator, _, _, _ = _make_orchestrator()
        orchestrator._settings.container_manager_type = "docker"

        mock_proxy = MagicMock()
        orchestrator._proxies["ws-docker-001"] = mock_proxy

        from app.services.proxy.credential_proxy import McpHeaderRule

        rules = {
            "test-server": McpHeaderRule(
                real_base_url="https://api.example.com",
                headers={"Authorization": "Bearer token123"},
            )
        }

        await orchestrator.update_mcp_header_rules("ws-docker-001", rules)

        mock_proxy.update_mcp_header_rules.assert_called_once_with(rules)
