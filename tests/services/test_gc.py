"""
GarbageCollector ユニットテスト

ContainerGarbageCollector の TTL 判定と孤立コンテナ検出をテストする。
Docker / ECS 両モード対応。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.container.models import ContainerInfo, ContainerStatus


def _make_gc(manager_type: str = "docker"):
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
    settings.container_manager_type = manager_type
    settings.container_inactive_ttl = 3600
    settings.container_absolute_ttl = 28800
    settings.container_gc_interval = 60
    settings.container_grace_period = 30

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


class TestEcsGC:
    """ECS モード GC テスト"""

    @pytest.mark.asyncio
    async def test_collect_ecs_destroys_expired_containers(self):
        """ECS モードで期限切れコンテナが Redis SCAN 経由で破棄される"""
        gc, mock_lifecycle, mock_redis = _make_gc(manager_type="ecs")

        # Redis SCAN が workspace:container:conv-001 を返す
        mock_redis.scan.return_value = (0, ["workspace:container:conv-001"])

        expired_info = ContainerInfo(
            id="ws-ecs-expired",
            conversation_id="conv-001",
            agent_socket="http://10.0.1.5:9000",
            proxy_socket="",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_active_at=datetime.now(timezone.utc) - timedelta(hours=2),
            status=ContainerStatus.IDLE,
            manager_type="ecs",
        )
        mock_redis.hgetall.return_value = expired_info.to_redis_hash()
        mock_redis.exists.return_value = False  # WarmPool情報なし

        await gc._collect_ecs()

        # コンテナが破棄される
        mock_lifecycle.destroy_container.assert_called_once()

    @pytest.mark.asyncio
    async def test_collect_ecs_skips_warmpool_containers(self):
        """ECS モードで WarmPool 管理のコンテナはスキップされる"""
        gc, mock_lifecycle, mock_redis = _make_gc(manager_type="ecs")

        mock_redis.scan.return_value = (0, ["workspace:container:conv-001"])

        info = ContainerInfo(
            id="ws-ecs-warm",
            conversation_id="conv-001",
            agent_socket="http://10.0.1.5:9000",
            proxy_socket="",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_active_at=datetime.now(timezone.utc) - timedelta(hours=2),
            status=ContainerStatus.IDLE,
            manager_type="ecs",
        )
        mock_redis.hgetall.return_value = info.to_redis_hash()
        mock_redis.exists.return_value = True  # WarmPool管理

        await gc._collect_ecs()

        mock_lifecycle.destroy_container.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_orphan_ecs_tasks_destroys_old_orphans(self):
        """古い孤立 ECS タスクが検出・破棄される"""
        gc, mock_lifecycle, mock_redis = _make_gc(manager_type="ecs")

        old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        mock_lifecycle.list_workspace_containers.return_value = [
            {
                "Name": "ws-orphan-ecs",
                "Created": old_time,
                "Config": {"Labels": {}},
            }
        ]
        # Redis にどのキーも存在しない（孤立状態）
        mock_redis.exists.return_value = False

        destroyed = await gc._detect_orphan_ecs_tasks()

        assert destroyed == 1
        mock_lifecycle.destroy_container.assert_called_once()

    @pytest.mark.asyncio
    async def test_detect_orphan_ecs_tasks_skips_new_tasks(self):
        """新しい ECS タスクは孤立判定されない"""
        gc, mock_lifecycle, mock_redis = _make_gc(manager_type="ecs")

        now = datetime.now(timezone.utc)
        mock_lifecycle.list_workspace_containers.return_value = [
            {
                "Name": "ws-new-ecs",
                "Created": now,
                "Config": {"Labels": {}},
            }
        ]
        mock_redis.exists.return_value = False

        destroyed = await gc._detect_orphan_ecs_tasks()

        assert destroyed == 0
        mock_lifecycle.destroy_container.assert_not_called()

    @pytest.mark.asyncio
    async def test_graceful_destroy_ecs_deletes_task_mapping_key(self):
        """ECS コンテナの graceful_destroy が ECS タスクマッピングキーも削除する"""
        gc, mock_lifecycle, mock_redis = _make_gc(manager_type="ecs")

        info = ContainerInfo(
            id="ws-ecs-gc",
            conversation_id="conv-001",
            agent_socket="http://10.0.1.5:9000",
            proxy_socket="",
            status=ContainerStatus.IDLE,
            manager_type="ecs",
        )

        await gc._graceful_destroy(info)

        # 3つの Redis キーが削除される: 正引き、逆引き、ECSタスクマッピング
        delete_calls = [str(c) for c in mock_redis.delete.call_args_list]
        assert any("workspace:container:conv-001" in c for c in delete_calls)
        assert any("workspace:container_reverse:ws-ecs-gc" in c for c in delete_calls)
        assert any("workspace:ecs_task:ws-ecs-gc" in c for c in delete_calls)

    @pytest.mark.asyncio
    async def test_graceful_destroy_docker_skips_task_mapping_key(self):
        """Docker コンテナの graceful_destroy は ECS タスクマッピングキーを削除しない"""
        gc, mock_lifecycle, mock_redis = _make_gc()

        info = ContainerInfo(
            id="ws-docker-gc",
            conversation_id="conv-002",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
            status=ContainerStatus.IDLE,
            manager_type="docker",
        )

        await gc._graceful_destroy(info)

        # ECSタスクマッピングキーは削除されない
        delete_calls = [str(c) for c in mock_redis.delete.call_args_list]
        assert not any("workspace:ecs_task:" in c for c in delete_calls)


class TestParseContainerAge:
    """_parse_container_age_seconds ヘルパーのテスト"""

    def test_parses_iso_string(self):
        """ISO 8601 文字列を正しくパースする"""
        from app.services.container.gc import _parse_container_age_seconds

        old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        age = _parse_container_age_seconds(old_time.isoformat())

        assert age is not None
        assert age > 590  # 10分 = 600秒 （テスト実行時のラグ考慮）
        assert age < 620

    def test_parses_datetime_object(self):
        """datetime オブジェクトを正しく処理する"""
        from app.services.container.gc import _parse_container_age_seconds

        old_time = datetime.now(timezone.utc) - timedelta(hours=1)
        age = _parse_container_age_seconds(old_time)

        assert age is not None
        assert age > 3590
        assert age < 3620

    def test_returns_none_for_invalid_string(self):
        """無効な文字列に対して None を返す"""
        from app.services.container.gc import _parse_container_age_seconds

        assert _parse_container_age_seconds("not-a-timestamp") is None

    def test_handles_z_suffix(self):
        """Z サフィックス付き ISO 文字列を処理する"""
        from app.services.container.gc import _parse_container_age_seconds

        old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        iso_z = old_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        age = _parse_container_age_seconds(iso_z)

        assert age is not None
        assert age > 290
        assert age < 320
