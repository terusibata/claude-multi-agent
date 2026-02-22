"""
GarbageCollector ユニットテスト

ContainerGarbageCollector の TTL 判定と孤立コンテナ検出をテストする。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.container.models import ContainerInfo, ContainerStatus


def _make_gc():
    from app.services.container.gc import ContainerGarbageCollector

    mock_lifecycle = AsyncMock()
    mock_lifecycle.list_workspace_containers = AsyncMock(return_value=[])
    mock_lifecycle.destroy_container = AsyncMock()

    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.hset = AsyncMock()
    mock_redis.delete = AsyncMock()
    mock_redis.scan = AsyncMock(return_value=(0, []))

    settings = MagicMock()
    settings.container_manager_type = "docker"
    settings.container_inactive_ttl = 3600
    settings.container_absolute_ttl = 28800
    settings.container_gc_interval = 60

    with patch("app.services.container.gc.get_settings", return_value=settings):
        gc = ContainerGarbageCollector(
            lifecycle=mock_lifecycle,
            redis=mock_redis,
            proxy_stop_callback=AsyncMock(),
        )
    return gc, mock_lifecycle, mock_redis


class TestShouldDestroy:
    """_should_destroy メソッドのテスト"""

    def test_inactive_ttl_exceeded(self):
        """非アクティブTTL超過でコンテナが破棄対象になる"""
        gc, _, _ = _make_gc()

        info = ContainerInfo(
            id="ws-old",
            conversation_id="conv-001",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_active_at=datetime.now(timezone.utc) - timedelta(hours=2),
            status=ContainerStatus.IDLE,
        )

        assert gc._should_destroy(info) is True

    def test_absolute_ttl_exceeded(self):
        """絶対TTL超過でコンテナが破棄対象になる"""
        gc, _, _ = _make_gc()

        info = ContainerInfo(
            id="ws-ancient",
            conversation_id="conv-001",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            created_at=datetime.now(timezone.utc) - timedelta(hours=10),
            last_active_at=datetime.now(timezone.utc),  # 最近アクティブ
            status=ContainerStatus.RUNNING,
        )

        assert gc._should_destroy(info) is True

    def test_draining_status(self):
        """DRAININGステータスで破棄対象になる"""
        gc, _, _ = _make_gc()

        info = ContainerInfo(
            id="ws-draining",
            conversation_id="conv-001",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            status=ContainerStatus.DRAINING,
        )

        assert gc._should_destroy(info) is True

    def test_healthy_container_not_destroyed(self):
        """正常なコンテナは破棄されない"""
        gc, _, _ = _make_gc()

        info = ContainerInfo(
            id="ws-healthy",
            conversation_id="conv-001",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            created_at=datetime.now(timezone.utc),
            last_active_at=datetime.now(timezone.utc),
            status=ContainerStatus.RUNNING,
        )

        assert gc._should_destroy(info) is False


class TestDockerOrphanDetection:
    """Docker モード孤立コンテナ検出のテスト（BUG-2 修正検証）"""

    @pytest.mark.asyncio
    async def test_skips_new_orphan_containers(self):
        """新しい孤立コンテナは破棄されない（ISO 8601 パース修正）"""
        gc, mock_lifecycle, mock_redis = _make_gc()

        now = datetime.now(timezone.utc)
        created_iso = now.isoformat()

        mock_lifecycle.list_workspace_containers.return_value = [
            {
                "Name": "/ws-new-orphan",
                "Config": {"Labels": {}},
                "Created": created_iso,
            }
        ]
        mock_redis.exists.return_value = False
        mock_redis.hgetall.return_value = {}

        await gc._collect_docker()

        # 新しいコンテナなので破棄されない
        mock_lifecycle.destroy_container.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroys_old_orphan_containers(self):
        """古い孤立コンテナは破棄される"""
        gc, mock_lifecycle, mock_redis = _make_gc()

        old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        created_iso = old_time.isoformat()

        mock_lifecycle.list_workspace_containers.return_value = [
            {
                "Name": "/ws-old-orphan",
                "Config": {"Labels": {}},
                "Created": created_iso,
            }
        ]
        mock_redis.exists.return_value = False
        mock_redis.hgetall.return_value = {}
        mock_redis.get.return_value = None

        await gc._collect_docker()

        # 古いコンテナなので破棄される
        mock_lifecycle.destroy_container.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_on_parse_failure(self):
        """タイムスタンプパース失敗時はコンテナを破棄しない（安全側）"""
        gc, mock_lifecycle, mock_redis = _make_gc()

        mock_lifecycle.list_workspace_containers.return_value = [
            {
                "Name": "/ws-bad-ts",
                "Config": {"Labels": {}},
                "Created": "not-a-timestamp",
            }
        ]
        mock_redis.exists.return_value = False
        mock_redis.hgetall.return_value = {}
        mock_redis.get.return_value = None

        await gc._collect_docker()

        # パース失敗時は安全側（破棄しない）
        mock_lifecycle.destroy_container.assert_not_called()
