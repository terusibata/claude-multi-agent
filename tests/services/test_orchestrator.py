"""
Orchestrator ユニットテスト

ContainerOrchestrator のコンテナ管理・復旧ロジックをテストする。
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.container.models import ContainerInfo, ContainerStatus


def _make_container_info(**overrides):
    defaults = {
        "id": "ws-test-001",
        "conversation_id": "conv-001",
        "agent_socket": "/tmp/agent.sock",
        "proxy_socket": "/tmp/proxy.sock",
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
    mock_redis.incr = AsyncMock(return_value=1)

    orchestrator = ContainerOrchestrator(mock_lifecycle, mock_warm_pool, mock_redis)
    return orchestrator, mock_lifecycle, mock_warm_pool, mock_redis


class TestGetOrCreate:
    """get_or_create メソッドのテスト"""

    @pytest.mark.asyncio
    async def test_returns_existing_healthy_container(self):
        """Redis に既存の健全なコンテナがある場合、それを返す"""
        orchestrator, mock_lifecycle, _, mock_redis = _make_orchestrator()
        info = _make_container_info()
        mock_redis.hgetall.return_value = info.to_redis_hash()

        result = await orchestrator.get_or_create("conv-001")

        assert result.id == "ws-test-001"
        assert result.conversation_id == "conv-001"
        mock_lifecycle.is_healthy.assert_called()

    @pytest.mark.asyncio
    async def test_replaces_unhealthy_container(self):
        """不健全なコンテナは破棄され、新しいものが WarmPool から取得される"""
        orchestrator, mock_lifecycle, mock_warm_pool, mock_redis = _make_orchestrator()

        old_info = _make_container_info(id="ws-old", status=ContainerStatus.READY)
        mock_redis.hgetall.side_effect = [
            old_info.to_redis_hash(),  # 最初の呼び出し: 既存コンテナ
            {},  # クリーンアップ後
        ]
        mock_lifecycle.is_healthy.return_value = False

        new_info = _make_container_info(id="ws-new")
        mock_warm_pool.acquire.return_value = new_info

        result = await orchestrator.get_or_create("conv-001")

        assert result.id == "ws-new"
        mock_lifecycle.destroy_container.assert_called()


class TestRecoveryLimit:
    """復旧試行回数制限のテスト（BUG-3 修正検証）"""

    @pytest.mark.asyncio
    async def test_recovery_blocked_after_max_attempts(self):
        """最大復旧回数を超えた場合、復旧がブロックされる"""
        orchestrator, mock_lifecycle, _, mock_redis = _make_orchestrator()

        info = _make_container_info()
        # Redis INCR が 3 を返す = 既に2回復旧済み → 3回目はブロック
        mock_redis.incr.return_value = 3

        with patch("app.services.container.orchestrator._MAX_RECOVERY_ATTEMPTS", 2):
            result_info, recovered = await orchestrator._recover_container(
                info, "conv-001", "test_error"
            )

        assert recovered is False

    @pytest.mark.asyncio
    async def test_recovery_allowed_within_limit(self):
        """復旧回数が制限内の場合、復旧が許可される"""
        orchestrator, mock_lifecycle, mock_warm_pool, mock_redis = _make_orchestrator()

        info = _make_container_info()
        mock_redis.incr.return_value = 1

        new_info = _make_container_info(id="ws-recovered")
        mock_redis.hgetall.return_value = {}
        mock_warm_pool.acquire.return_value = new_info

        result_info, recovered = await orchestrator._recover_container(
            info, "conv-001", "test_error"
        )

        assert recovered is True
        assert result_info.id == "ws-recovered"


class TestDestroyAll:
    """destroy_all メソッドのテスト"""

    @pytest.mark.asyncio
    async def test_destroy_all_stops_proxies_and_drains_pool(self):
        """destroy_all がプロキシ停止、プールドレイン、コンテナ破棄を行う"""
        orchestrator, mock_lifecycle, mock_warm_pool, _ = _make_orchestrator()

        mock_lifecycle.list_workspace_containers.return_value = [
            {"Name": "/ws-001"},
            {"Name": "ws-002"},  # ECS 形式（先頭スラッシュなし）
        ]

        await orchestrator.destroy_all()

        mock_warm_pool.drain.assert_called_once()
        # 2つのコンテナが破棄される
        assert mock_lifecycle.destroy_container.call_count == 2
