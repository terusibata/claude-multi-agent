"""
EcsContainerManager ユニットテスト

ECS モード固有のコンテナ管理ロジックをテストする。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError


def _make_ecs_manager():
    from app.services.container.ecs_manager import EcsContainerManager

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.hset = AsyncMock()
    mock_redis.expire = AsyncMock()
    mock_redis.delete = AsyncMock()

    settings = MagicMock()
    settings.ecs_cluster = "test-cluster"
    settings.ecs_task_definition = "test-task-def"
    settings.ecs_subnets = "subnet-123,subnet-456"
    settings.ecs_subnets_list = ["subnet-123", "subnet-456"]
    settings.ecs_security_groups = "sg-123"
    settings.ecs_security_groups_list = ["sg-123"]
    settings.ecs_capacity_provider = ""
    settings.ecs_agent_port = 9000
    settings.ecs_proxy_admin_port = 8081
    settings.aws_region = "us-west-2"
    settings.container_manager_type = "ecs"

    with patch("app.services.container.ecs_manager.get_settings", return_value=settings):
        manager = EcsContainerManager(mock_redis)

    return manager, mock_redis, settings


class TestGetAgentUrl:
    """_get_agent_url メソッドのテスト"""

    @pytest.mark.asyncio
    async def test_returns_url_from_redis(self):
        """Redis から正常にURLを取得する"""
        manager, mock_redis, _ = _make_ecs_manager()

        mock_redis.get.return_value = "conv-001"
        mock_redis.hgetall.return_value = {
            "agent_socket": "http://10.0.1.5:9000",
            "container_id": "ws-001",
            "conversation_id": "conv-001",
            "proxy_socket": "",
            "status": "ready",
            "manager_type": "ecs",
        }

        result = await manager._get_agent_url("ws-001")

        assert result == "http://10.0.1.5:9000"

    @pytest.mark.asyncio
    async def test_falls_back_to_describe_tasks(self):
        """Redis 逆引き失敗時に describe_tasks にフォールバック"""
        manager, mock_redis, _ = _make_ecs_manager()

        mock_redis.get.return_value = None  # Redis 逆引き失敗
        mock_redis.hgetall.return_value = {}

        # _resolve_task_arn と _get_task_ip をモック
        manager._resolve_task_arn = AsyncMock(return_value="arn:aws:ecs:us-west-2:123:task/abc")
        manager._get_task_ip = AsyncMock(return_value="10.0.1.10")

        result = await manager._get_agent_url("ws-fallback")

        assert result == "http://10.0.1.10:9000"

    @pytest.mark.asyncio
    async def test_returns_none_when_task_not_found(self):
        """タスクが見つからない場合 None を返す"""
        manager, mock_redis, _ = _make_ecs_manager()

        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}
        manager._resolve_task_arn = AsyncMock(return_value=None)

        result = await manager._get_agent_url("ws-missing")

        assert result is None


class TestEcsStartupValidation:
    """ECS モード起動時バリデーションのテスト（ISSUE-4 検証）"""

    @pytest.mark.asyncio
    async def test_ecs_mode_fails_without_required_settings(self):
        """ECS モードで必須設定が欠けている場合にエラーが発生する"""
        from app.core.lifespan import _create_container_manager

        settings = MagicMock()
        settings.container_manager_type = "ecs"
        settings.ecs_cluster = ""
        settings.ecs_task_definition = ""
        settings.ecs_subnets = ""

        mock_redis = AsyncMock()

        with pytest.raises(ValueError, match="ECSモードには以下の設定が必須"):
            _create_container_manager(settings, mock_redis)

    @pytest.mark.asyncio
    async def test_docker_mode_does_not_require_ecs_settings(self):
        """Docker モードでは ECS 設定は不要"""
        from app.core.lifespan import _create_container_manager

        settings = MagicMock()
        settings.container_manager_type = "docker"
        settings.docker_socket_path = "unix:///var/run/docker.sock"
        settings.ecs_cluster = ""  # 空でもOK

        mock_redis = AsyncMock()

        # aiodocker はローカルインポートされるため sys.modules でモック
        mock_aiodocker = MagicMock()
        mock_aiodocker.Docker.return_value = MagicMock()
        with patch.dict("sys.modules", {"aiodocker": mock_aiodocker}):
            lifecycle, docker_client = _create_container_manager(settings, mock_redis)

        assert docker_client is not None


def _make_client_error(code: str, message: str = "error") -> ClientError:
    """テスト用の botocore ClientError を生成"""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="StopTask",
    )


class TestDestroyContainer:
    """destroy_container のエラーハンドリングテスト（Issue 3 検証）"""

    @pytest.mark.asyncio
    async def test_ignores_invalid_parameter_exception(self):
        """InvalidParameterException（既に停止済み）は例外を伝播しない"""
        manager, mock_redis, _ = _make_ecs_manager()

        mock_redis.get.return_value = "arn:aws:ecs:us-west-2:123:task/cluster/abc"

        mock_ecs = AsyncMock()
        mock_ecs.stop_task.side_effect = _make_client_error("InvalidParameterException")
        manager._ecs_client = mock_ecs
        manager._ecs_ctx = MagicMock()

        # 例外が伝播しないことを確認
        await manager.destroy_container("ws-already-stopped")
        mock_redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_raises_on_other_client_error(self):
        """InvalidParameterException 以外の ClientError は re-raise する"""
        manager, mock_redis, _ = _make_ecs_manager()

        mock_redis.get.return_value = "arn:aws:ecs:us-west-2:123:task/cluster/abc"

        mock_ecs = AsyncMock()
        mock_ecs.stop_task.side_effect = _make_client_error("AccessDeniedException")
        manager._ecs_client = mock_ecs
        manager._ecs_ctx = MagicMock()

        with pytest.raises(ClientError):
            await manager.destroy_container("ws-access-denied")
