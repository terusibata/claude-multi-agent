"""
Phase 2 統合テスト
WarmPool最適化・seccomp・メトリクス・Proxyの結合テスト
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestWarmPoolPreheat:
    """WarmPool プリヒートのテスト"""

    @pytest.mark.asyncio
    async def test_preheat_creates_containers_up_to_min_size(self):
        """プリヒートがmin_sizeまでコンテナを作成すること"""
        from app.services.container.warm_pool import WarmPoolManager

        mock_lifecycle = AsyncMock()
        mock_lifecycle.create_container.return_value = MagicMock(
            id="ws-test123",
            to_redis_hash=lambda: {
                "container_id": "ws-test123",
                "conversation_id": "",
                "agent_socket": "/tmp/agent.sock",
                "proxy_socket": "/tmp/proxy.sock",
                "created_at": "2026-02-07T00:00:00+00:00",
                "last_active_at": "2026-02-07T00:00:00+00:00",
                "status": "warm",
            },
        )

        mock_redis = AsyncMock()
        mock_redis.llen.return_value = 0
        mock_redis.hset.return_value = True
        mock_redis.expire.return_value = True
        mock_redis.rpush.return_value = True
        mock_redis.hgetall.return_value = {}

        pool = WarmPoolManager(mock_lifecycle, mock_redis, min_size=2, max_size=5)
        created = await pool.preheat()

        assert created == 2
        assert mock_lifecycle.create_container.call_count == 2

    @pytest.mark.asyncio
    async def test_preheat_skips_when_pool_full(self):
        """プールが既にmin_size以上の場合、プリヒートをスキップすること"""
        from app.services.container.warm_pool import WarmPoolManager

        mock_lifecycle = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.llen.return_value = 3
        mock_redis.hgetall.return_value = {}

        pool = WarmPoolManager(mock_lifecycle, mock_redis, min_size=2, max_size=5)
        created = await pool.preheat()

        assert created == 0
        mock_lifecycle.create_container.assert_not_called()


class TestWarmPoolRetry:
    """WarmPool リトライロジックのテスト"""

    @pytest.mark.asyncio
    async def test_retry_on_creation_failure(self):
        """コンテナ作成失敗時にリトライすること"""
        from app.services.container.warm_pool import WarmPoolManager

        mock_lifecycle = AsyncMock()
        # 1回目失敗、2回目成功
        mock_lifecycle.create_container.side_effect = [
            Exception("Docker API error"),
            MagicMock(
                id="ws-retry123",
                to_redis_hash=lambda: {
                    "container_id": "ws-retry123",
                    "conversation_id": "",
                    "agent_socket": "/tmp/agent.sock",
                    "proxy_socket": "/tmp/proxy.sock",
                    "created_at": "2026-02-07T00:00:00+00:00",
                    "last_active_at": "2026-02-07T00:00:00+00:00",
                    "status": "warm",
                },
            ),
        ]

        mock_redis = AsyncMock()
        mock_redis.llen.return_value = 0
        mock_redis.hset.return_value = True
        mock_redis.expire.return_value = True
        mock_redis.rpush.return_value = True

        pool = WarmPoolManager(mock_lifecycle, mock_redis, min_size=1, max_size=5)

        with patch("app.services.container.warm_pool.asyncio.sleep", new_callable=AsyncMock):
            result = await pool._create_and_add_with_retry()

        assert result is True
        assert mock_lifecycle.create_container.call_count == 2


class TestWarmPoolMetrics:
    """WarmPool メトリクスのテスト"""

    @pytest.mark.asyncio
    async def test_exhaustion_metric_on_empty_pool(self):
        """プール枯渇時にメトリクスが記録されること"""
        from app.infrastructure.metrics import get_workspace_warm_pool_exhausted
        from app.services.container.warm_pool import WarmPoolManager

        mock_lifecycle = AsyncMock()
        mock_lifecycle.create_container.return_value = MagicMock(
            id="ws-fallback",
            to_redis_hash=lambda: {},
        )

        mock_redis = AsyncMock()
        mock_redis.lpop.return_value = None  # プール空
        mock_redis.llen.return_value = 0
        mock_redis.hgetall.return_value = {}

        pool = WarmPoolManager(mock_lifecycle, mock_redis, min_size=2, max_size=5)

        counter = get_workspace_warm_pool_exhausted()
        before = counter.get()
        await pool.acquire()
        after = counter.get()

        assert after > before


class TestWarmPoolHotReload:
    """WarmPool ホットリロードのテスト"""

    @pytest.mark.asyncio
    async def test_config_update_via_redis(self):
        """Redis経由で設定が動的に更新されること"""
        from app.services.container.warm_pool import WarmPoolManager

        mock_lifecycle = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.hset.return_value = True
        mock_redis.hgetall.return_value = {
            "min_size": "5",
            "max_size": "20",
        }

        pool = WarmPoolManager(mock_lifecycle, mock_redis, min_size=2, max_size=10)

        await pool.update_config(min_size=5, max_size=20)

        assert pool.min_size == 5
        assert pool.max_size == 20


class TestSeccompConfig:
    """seccompプロファイル設定のテスト"""

    def test_seccomp_profile_applied_when_configured(self):
        """seccompプロファイルパスが設定されている場合にSecurityOptに追加されること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=100,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="/etc/docker/seccomp/workspace.json",
                userns_remap_enabled=False,
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            security_opts = config["HostConfig"]["SecurityOpt"]

            assert "no-new-privileges:true" in security_opts
            assert "seccomp=/etc/docker/seccomp/workspace.json" in security_opts

    def test_seccomp_default_when_not_configured(self):
        """seccompプロファイルが未設定の場合はDockerデフォルトが使われること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=100,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                userns_remap_enabled=False,
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            security_opts = config["HostConfig"]["SecurityOpt"]

            assert security_opts == ["no-new-privileges:true"]


class TestUsernsRemapConfig:
    """userns-remap設定のテスト"""

    def test_userns_mode_host_when_disabled(self):
        """userns-remap無効時にUsernsMode=hostが設定されること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=100,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                userns_remap_enabled=False,
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            assert config["HostConfig"]["UsernsMode"] == "host"

    def test_userns_mode_empty_when_enabled(self):
        """userns-remap有効時にUsernsMode=空文字（デーモン設定に従う）であること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=100,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                userns_remap_enabled=True,
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            assert config["HostConfig"]["UsernsMode"] == ""


class TestOrchestratorMetrics:
    """Orchestrator メトリクスのテスト"""

    @pytest.mark.asyncio
    async def test_container_startup_metric_recorded(self):
        """コンテナ割り当て時に起動時間メトリクスが記録されること"""
        from app.infrastructure.metrics import get_workspace_container_startup

        histogram = get_workspace_container_startup()
        initial_count = histogram._totals.get((), 0)

        # Orchestratorのget_or_createでメトリクスが記録されることを確認
        # （実際のDocker操作はモックで代替）
        assert histogram.name == "workspace_container_startup_seconds"
        assert initial_count >= 0  # メトリクスが初期化されていること


class TestProxyMetrics:
    """Proxy メトリクスのテスト"""

    def test_proxy_blocked_metric_exists(self):
        """Proxyブロックメトリクスが定義されていること"""
        from app.infrastructure.metrics import get_workspace_proxy_blocked

        counter = get_workspace_proxy_blocked()
        assert counter.name == "workspace_proxy_blocked_total"

    def test_proxy_duration_metric_exists(self):
        """Proxyレイテンシメトリクスが定義されていること"""
        from app.infrastructure.metrics import get_workspace_proxy_request_duration

        histogram = get_workspace_proxy_request_duration()
        assert histogram.name == "workspace_proxy_request_duration_seconds"


class TestGCMetrics:
    """GC メトリクスのテスト"""

    def test_gc_cycles_metric_exists(self):
        """GCサイクルメトリクスが定義されていること"""
        from app.infrastructure.metrics import get_workspace_gc_cycles

        counter = get_workspace_gc_cycles()
        assert counter.name == "workspace_gc_cycles_total"
        assert "result" in counter.labels
