"""
Phase 4 統合テスト
SDK API準拠、コンテナ通信経路、リソース管理、設定ベストプラクティスの検証
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestContainerEnvVars:
    """コンテナ環境変数のテスト"""

    def test_proxy_uses_tcp_not_unix_socket(self):
        """環境変数がTCPプロキシ（socat経由）を使用すること"""
        with patch("app.services.container.config.get_settings") as mock_settings, \
             patch("app.services.container.config._load_seccomp_profile", return_value='{}'):
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="deployment/seccomp/workspace-seccomp.json",
                apparmor_profile_name="",
                aws_region="us-west-2",
                userns_remap_enabled=True,
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            env_list = config["Env"]

            # http+unix:// が使われていないことを確認
            for env_var in env_list:
                assert "http+unix://" not in env_var, f"http+unix:// found in: {env_var}"

            # TCP proxy が使われていることを確認
            assert "ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8080" in env_list
            assert "HTTP_PROXY=http://127.0.0.1:8080" in env_list
            assert "HTTPS_PROXY=http://127.0.0.1:8080" in env_list

    def test_no_proxy_localhost(self):
        """NO_PROXY にlocalhostが含まれること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                userns_remap_enabled=True,
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            env_list = config["Env"]

            assert "NO_PROXY=localhost,127.0.0.1" in env_list

    def test_workspace_tmpfs_writable(self):
        """/workspace が Tmpfs マウントで書き込み可能なこと (ReadonlyRootfs対応)"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                userns_remap_enabled=True,
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            tmpfs = config["HostConfig"]["Tmpfs"]

            assert "/workspace" in tmpfs
            # noexec がないこと（コード実行が必要）
            assert "noexec" not in tmpfs["/workspace"]
            # rw であること
            assert "rw" in tmpfs["/workspace"]


class TestDefaultSettings:
    """デフォルト設定のテスト"""

    def test_userns_remap_disabled_by_default(self):
        """userns_remap_enabled がデフォルトで False であること（有効化にはDocker daemon設定が必要）"""
        with patch.dict("os.environ", {}, clear=False):
            from app.config import Settings
            s = Settings(
                _env_file=None,
                database_url="postgresql+asyncpg://test:test@localhost/test",
            )
            assert s.userns_remap_enabled is False

    def test_seccomp_profile_path_has_default(self):
        """seccomp_profile_path にデフォルト値が設定されていること"""
        with patch.dict("os.environ", {}, clear=False):
            from app.config import Settings
            s = Settings(
                _env_file=None,
                database_url="postgresql+asyncpg://test:test@localhost/test",
            )
            assert s.seccomp_profile_path == "deployment/seccomp/workspace-seccomp.json"

    def test_pids_limit_increased_for_sdk(self):
        """PidsLimit がSDK CLIサブプロセス対応で256に設定されていること"""
        with patch.dict("os.environ", {}, clear=False):
            from app.config import Settings
            s = Settings(
                _env_file=None,
                database_url="postgresql+asyncpg://test:test@localhost/test",
            )
            assert s.container_pids_limit == 256


class TestWorkspaceAgentModels:
    """workspace_agent モデルのテスト"""

    def test_execute_request_has_max_turns(self):
        """ExecuteRequest が max_turns フィールドを持つこと"""
        from workspace_agent.models import ExecuteRequest

        req = ExecuteRequest(user_input="hello")
        assert hasattr(req, "max_turns")
        assert req.max_turns is None

    def test_execute_request_has_allowed_tools(self):
        """ExecuteRequest が allowed_tools フィールドを持つこと"""
        from workspace_agent.models import ExecuteRequest

        req = ExecuteRequest(user_input="hello", allowed_tools=["Bash", "Read"])
        assert req.allowed_tools == ["Bash", "Read"]

    def test_execute_request_no_legacy_fields(self):
        """ExecuteRequest にレガシーフィールド(max_iterations, budget_tokens)がないこと"""
        from workspace_agent.models import ExecuteRequest

        req = ExecuteRequest(user_input="hello")
        assert not hasattr(req, "max_iterations")
        assert not hasattr(req, "budget_tokens")


class TestSDKClientOptions:
    """SDK クライアントオプションのテスト"""

    def test_build_sdk_options_uses_claude_agent_options(self):
        """_build_sdk_options が ClaudeAgentOptions を使用すること"""
        with patch("workspace_agent.sdk_client.os.environ", {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8080"}):
            try:
                from claude_agent_sdk import ClaudeAgentOptions
            except ImportError:
                pytest.skip("claude-agent-sdk not installed")

            from workspace_agent.models import ExecuteRequest
            from workspace_agent.sdk_client import _build_sdk_options

            req = ExecuteRequest(user_input="test", model="claude-sonnet-4-5-20250929")
            options = _build_sdk_options(req)

            assert isinstance(options, ClaudeAgentOptions)
            assert options.permission_mode == "bypassPermissions"

    def test_build_sdk_options_sets_env(self):
        """_build_sdk_options がenv にProxy設定を含めること"""
        with patch("workspace_agent.sdk_client.os.environ", {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8080",
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }):
            try:
                from claude_agent_sdk import ClaudeAgentOptions
            except ImportError:
                pytest.skip("claude-agent-sdk not installed")

            from workspace_agent.models import ExecuteRequest
            from workspace_agent.sdk_client import _build_sdk_options

            req = ExecuteRequest(user_input="test")
            options = _build_sdk_options(req)

            assert "ANTHROPIC_BASE_URL" in options.env
            assert options.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"


class TestOrchestratorDestroyAll:
    """Orchestrator destroy_all のテスト"""

    @pytest.mark.asyncio
    async def test_destroy_all_stops_proxies_first(self):
        """destroy_all が全Proxyを先に停止すること"""
        from app.services.container.orchestrator import ContainerOrchestrator

        mock_lifecycle = AsyncMock()
        mock_lifecycle.list_workspace_containers.return_value = []
        mock_warm_pool = AsyncMock()
        mock_redis = AsyncMock()

        orchestrator = ContainerOrchestrator(mock_lifecycle, mock_warm_pool, mock_redis)

        # Proxyをモックで登録
        mock_proxy = AsyncMock()
        orchestrator._proxies["ws-test1"] = mock_proxy
        orchestrator._proxies["ws-test2"] = mock_proxy

        await orchestrator.destroy_all()

        # Proxyのstopが呼ばれたことを確認
        assert mock_proxy.stop.call_count >= 2


class TestGCOrphanContainerHandling:
    """GC 孤立コンテナ回収のテスト"""

    @pytest.mark.asyncio
    async def test_gc_destroys_old_orphan_containers(self):
        """GCが古い孤立コンテナ（Redisメタデータなし）を回収すること"""
        import time

        from app.services.container.gc import ContainerGarbageCollector

        mock_lifecycle = AsyncMock()
        # 古いコンテナ（5分以上前に作成）
        old_created = time.time() - 600
        mock_lifecycle.list_workspace_containers.return_value = [
            {
                "Name": "/ws-orphan123",
                "Config": {"Labels": {"workspace.conversation_id": "conv-none"}},
                "State": {"Running": True},
                "Created": str(old_created),
            }
        ]

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0  # WarmPoolに属していない
        mock_redis.hgetall.return_value = {}  # Redisにメタデータなし
        mock_redis.get.return_value = None  # 逆引きマッピングなし

        gc = ContainerGarbageCollector(mock_lifecycle, mock_redis)
        await gc._collect()

        # 孤立コンテナが破棄されたことを確認
        mock_lifecycle.destroy_container.assert_called_once_with("ws-orphan123", grace_period=5)


class TestWarmPoolTaskTracking:
    """WarmPool バックグラウンドタスク追跡のテスト"""

    @pytest.mark.asyncio
    async def test_background_tasks_tracked(self):
        """バックグラウンドタスクが_background_tasksセットで追跡されること"""
        from app.services.container.warm_pool import WarmPoolManager

        mock_lifecycle = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.llen.return_value = 5

        pool = WarmPoolManager(mock_lifecycle, mock_redis, min_size=2, max_size=10)
        assert hasattr(pool, "_background_tasks")
        assert isinstance(pool._background_tasks, set)
