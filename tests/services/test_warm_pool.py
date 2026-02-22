"""
WarmPoolManager ユニットテスト

プリヒート、取得、補充、ドレインのロジックをテストする。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.container.models import ContainerInfo, ContainerStatus


def _make_warm_pool():
    from app.services.container.warm_pool import WarmPoolManager

    mock_lifecycle = AsyncMock()
    mock_lifecycle.create_container = AsyncMock()
    mock_lifecycle.destroy_container = AsyncMock()
    mock_lifecycle.is_healthy = AsyncMock(return_value=True)
    mock_lifecycle.wait_for_agent_ready = AsyncMock(return_value=True)

    mock_redis = AsyncMock()
    mock_redis.llen = AsyncMock(return_value=0)
    mock_redis.lpop = AsyncMock(return_value=None)
    mock_redis.rpush = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.hset = AsyncMock()
    mock_redis.expire = AsyncMock()
    mock_redis.delete = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    settings = MagicMock()
    settings.container_manager_type = "docker"
    settings.warm_pool_min_size = 2
    settings.warm_pool_max_size = 10
    settings.ecs_warm_pool_min_size = 2
    settings.ecs_warm_pool_max_size = 20
    settings.ecs_run_task_concurrency = 10

    with patch("app.services.container.warm_pool.get_settings", return_value=settings):
        pool = WarmPoolManager(mock_lifecycle, mock_redis)

    return pool, mock_lifecycle, mock_redis


class TestAcquire:
    """acquire メソッドのテスト"""

    @pytest.mark.asyncio
    async def test_acquire_returns_healthy_container_from_pool(self):
        """プール内の健全なコンテナを返す"""
        pool, mock_lifecycle, mock_redis = _make_warm_pool()

        info = ContainerInfo(
            id="ws-pool-001",
            conversation_id="",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            status=ContainerStatus.WARM,
        )
        mock_redis.lpop.return_value = "ws-pool-001"
        mock_redis.hgetall.return_value = info.to_redis_hash()
        mock_lifecycle.is_healthy.return_value = True

        result = await pool.acquire()

        assert result.id == "ws-pool-001"

    @pytest.mark.asyncio
    async def test_acquire_skips_unhealthy_containers(self):
        """不健全なコンテナはスキップされて健全なコンテナが返る"""
        pool, mock_lifecycle, mock_redis = _make_warm_pool()

        unhealthy_info = ContainerInfo(
            id="ws-unhealthy",
            conversation_id="",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            status=ContainerStatus.WARM,
        )
        healthy_info = ContainerInfo(
            id="ws-healthy",
            conversation_id="",
            agent_socket="/tmp/agent2.sock",
            proxy_socket="/tmp/proxy2.sock",
            status=ContainerStatus.WARM,
        )

        # lpop: 1回目→不健全、2回目→健全
        mock_redis.lpop.side_effect = ["ws-unhealthy", "ws-healthy"]
        mock_redis.hgetall = AsyncMock(
            side_effect=lambda key: (
                unhealthy_info.to_redis_hash()
                if "ws-unhealthy" in key
                else healthy_info.to_redis_hash()
                if "ws-healthy" in key
                else {}
            )
        )
        mock_lifecycle.is_healthy.side_effect = [False, True]

        result = await pool.acquire()

        # 不健全なコンテナをスキップし、健全なコンテナが返る
        assert result.id == "ws-healthy"
        # is_healthy が2回呼ばれた（1回目: False、2回目: True）
        assert mock_lifecycle.is_healthy.call_count == 2

    @pytest.mark.asyncio
    async def test_acquire_creates_new_when_pool_empty(self):
        """プールが空の場合、新しいコンテナを作成"""
        pool, mock_lifecycle, mock_redis = _make_warm_pool()

        new_info = ContainerInfo(
            id="ws-new",
            conversation_id="",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            status=ContainerStatus.WARM,
        )
        mock_redis.lpop.return_value = None  # プール空
        mock_lifecycle.create_container.return_value = new_info

        result = await pool.acquire()

        assert result.id == "ws-new"
        mock_lifecycle.create_container.assert_called_once()


class TestDrain:
    """drain メソッドのテスト"""

    @pytest.mark.asyncio
    async def test_drain_destroys_all_pool_containers(self):
        """ドレインが全プールコンテナを破棄する"""
        pool, mock_lifecycle, mock_redis = _make_warm_pool()

        info1 = ContainerInfo(
            id="ws-drain-001",
            conversation_id="",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            status=ContainerStatus.WARM,
        )

        # 1回目: コンテナ取得、2回目: プール空
        mock_redis.lpop.side_effect = ["ws-drain-001", None]
        mock_redis.hgetall.return_value = info1.to_redis_hash()

        await pool.drain()

        mock_lifecycle.destroy_container.assert_called()
