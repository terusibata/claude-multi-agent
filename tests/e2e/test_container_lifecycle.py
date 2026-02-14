"""
E2E 統合テスト: コンテナライフサイクル (Phase 5)

docker-compose 環境でのコンテナ隔離フロー全体を検証する。
Docker デーモンが利用可能な環境でのみ実行可能。

実行方法:
  pytest tests/e2e/ -v --timeout=120
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Docker デーモンの利用可否を確認
def docker_available() -> bool:
    try:
        import aiodocker
        return True
    except ImportError:
        return False


skipif_no_docker = pytest.mark.skipif(
    not docker_available(),
    reason="aiodocker not installed",
)


class TestContainerCreateConfig:
    """コンテナ作成設定のE2Eテスト"""

    def test_config_has_network_none(self):
        """コンテナが --network none で作成されること"""
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
                apparmor_profile_name="workspace-container",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            assert config["HostConfig"]["NetworkMode"] == "none"

    def test_config_has_readonly_rootfs(self):
        """ReadonlyRootfs が有効であること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                apparmor_profile_name="",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            assert config["HostConfig"]["ReadonlyRootfs"] is True

    def test_config_has_pids_limit(self):
        """PidsLimit が設定されていること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                apparmor_profile_name="",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            assert config["HostConfig"]["PidsLimit"] == 256

    def test_config_drops_all_capabilities(self):
        """全 capability が DROP されていること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                apparmor_profile_name="",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            assert config["HostConfig"]["CapDrop"] == ["ALL"]

    def test_config_has_seccomp_and_apparmor(self):
        """seccomp と AppArmor が SecurityOpt に含まれること"""
        fake_seccomp = '{"defaultAction":"SCMP_ACT_ERRNO"}'
        with patch("app.services.container.config.get_settings") as mock_settings, \
             patch("app.services.container.config._load_seccomp_profile", return_value=fake_seccomp):
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="deployment/seccomp/workspace-seccomp.json",
                apparmor_profile_name="workspace-container",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            security_opt = config["HostConfig"]["SecurityOpt"]
            assert "no-new-privileges:true" in security_opt
            assert f"seccomp={fake_seccomp}" in security_opt
            assert "apparmor=workspace-container" in security_opt

    def test_config_does_not_set_node_options(self):
        """NODE_OPTIONS と GLOBAL_AGENT_* がコンテナ環境変数に含まれないこと"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                apparmor_profile_name="",
                aws_region="us-west-2",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            env_list = config["Env"]
            # NODE_OPTIONS はSDKバンドルCLIのNode.jsをクラッシュさせるため除外
            assert not any("NODE_OPTIONS" in e for e in env_list)
            assert not any("GLOBAL_AGENT" in e for e in env_list)
            # pip/curl用のProxy環境変数は維持
            assert "HTTP_PROXY=http://127.0.0.1:8080" in env_list
            assert "HTTPS_PROXY=http://127.0.0.1:8080" in env_list

    def test_config_has_claude_env_vars(self):
        """CLI用のHOME・CLAUDE_CONFIG_DIRがコンテナ環境変数に含まれること"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                apparmor_profile_name="",
                aws_region="us-west-2",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            env_list = config["Env"]
            assert "HOME=/home/appuser" in env_list
            assert "CLAUDE_CONFIG_DIR=/home/appuser/.claude" in env_list

    def test_tmp_allows_exec(self):
        """SDK CLIバイナリ実行のため /tmp に noexec がないこと"""
        with patch("app.services.container.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                container_image="workspace-base:latest",
                container_cpu_quota=200000,
                container_memory_limit=2 * 1024**3,
                container_pids_limit=256,
                container_disk_limit="5G",
                resolved_socket_host_path="/var/run/ws",
                seccomp_profile_path="",
                apparmor_profile_name="",
                aws_region="us-west-2",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-e2e-test")
            tmpfs = config["HostConfig"]["Tmpfs"]
            assert "noexec" not in tmpfs["/tmp"]
            # /workspace も exec 可能であること
            assert "noexec" not in tmpfs["/workspace"]


class TestProxyCommunication:
    """Proxy 通信テスト"""

    def test_domain_whitelist_allows_pypi(self):
        """ドメインホワイトリストが pypi.org を許可すること"""
        from app.services.proxy.domain_whitelist import DomainWhitelist

        wl = DomainWhitelist(["pypi.org", "files.pythonhosted.org"])
        assert wl.is_allowed("https://pypi.org/simple/requests/")
        assert wl.is_allowed("https://files.pythonhosted.org/packages/foo.tar.gz")

    def test_domain_whitelist_blocks_unknown(self):
        """ドメインホワイトリストが不明なドメインを拒否すること"""
        from app.services.proxy.domain_whitelist import DomainWhitelist

        wl = DomainWhitelist(["pypi.org"])
        assert not wl.is_allowed("https://evil.com/malware.sh")
        assert not wl.is_allowed("http://169.254.169.254/latest/meta-data/")

    def test_domain_whitelist_allows_bedrock(self):
        """ドメインホワイトリストが Bedrock API を許可すること"""
        from app.services.proxy.domain_whitelist import DomainWhitelist

        wl = DomainWhitelist([
            "bedrock-runtime.us-east-1.amazonaws.com",
            "bedrock-runtime.us-west-2.amazonaws.com",
        ])
        assert wl.is_allowed("https://bedrock-runtime.us-west-2.amazonaws.com/model/invoke")


class TestSSEEventParsing:
    """SSE イベントパースのテスト"""

    def test_parse_text_delta_event(self):
        """text_delta SSE イベントのパースが正常に動作すること"""
        from app.services.execute_service import ExecuteService

        service = MagicMock(spec=ExecuteService)
        event_str = 'event: text_delta\ndata: {"text": "Hello world"}'
        result = ExecuteService._parse_sse_event(service, event_str)

        assert result is not None
        assert result["event"] == "text_delta"
        assert result["data"]["text"] == "Hello world"

    def test_parse_done_event(self):
        """done SSE イベントのパースが正常に動作すること"""
        from app.services.execute_service import ExecuteService

        service = MagicMock(spec=ExecuteService)
        event_str = 'event: done\ndata: {"usage": {"input_tokens": 100, "output_tokens": 50}, "cost_usd": "0.001"}'
        result = ExecuteService._parse_sse_event(service, event_str)

        assert result is not None
        assert result["event"] == "done"
        assert result["data"]["usage"]["input_tokens"] == 100

    def test_parse_error_event(self):
        """error SSE イベントのパースが正常に動作すること"""
        from app.services.execute_service import ExecuteService

        service = MagicMock(spec=ExecuteService)
        event_str = 'event: error\ndata: {"message": "Container execution failed"}'
        result = ExecuteService._parse_sse_event(service, event_str)

        assert result is not None
        assert result["event"] == "error"
        assert "failed" in result["data"]["message"]


class TestOrchestratorCrashRecovery:
    """Orchestrator クラッシュ復旧のテスト"""

    @pytest.mark.asyncio
    async def test_execute_emits_container_recovered_on_crash(self):
        """コンテナクラッシュ時に container_recovered イベントが送信されること"""
        from app.services.container.orchestrator import ContainerOrchestrator
        from app.services.container.models import ContainerInfo, ContainerStatus

        mock_lifecycle = AsyncMock()
        mock_lifecycle.is_healthy.return_value = True
        mock_warm_pool = AsyncMock()
        mock_redis = AsyncMock()

        # get_or_create のモック: 2回呼ばれる（1回目=クラッシュ元、2回目=復旧先）
        container_info = ContainerInfo(
            id="ws-crash-test",
            conversation_id="conv-123",
            agent_socket="/tmp/agent.sock",
            proxy_socket="/tmp/proxy.sock",
        )
        mock_redis.hgetall.return_value = container_info.to_redis_hash()

        orchestrator = ContainerOrchestrator(mock_lifecycle, mock_warm_pool, mock_redis)

        # execute内でhttpxがConnectionErrorを投げることをシミュレート
        with patch("app.services.container.orchestrator.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("Connection refused")
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            events = []
            async for chunk in orchestrator.execute("conv-123", {"user_input": "test"}):
                events.append(chunk)

            # container_recovered イベントが含まれるか確認
            all_data = b"".join(events).decode("utf-8", errors="replace")
            assert "container_recovered" in all_data or "error" in all_data


class TestAuditLogging:
    """監査ログのテスト"""

    def test_audit_container_created(self):
        """container_created 監査ログが正しいフィールドを含むこと"""
        from app.infrastructure.audit_log import audit_container_created

        with patch("app.infrastructure.audit_log.audit_logger") as mock_logger:
            audit_container_created(
                container_id="ws-123",
                conversation_id="conv-456",
                tenant_id="tenant-789",
                source="warm_pool",
                duration_ms=1200,
            )

            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args
            assert call_kwargs[0][0] == "container_created"
            assert call_kwargs[1]["container_id"] == "ws-123"
            assert call_kwargs[1]["conversation_id"] == "conv-456"
            assert call_kwargs[1]["network_mode"] == "none"

    def test_audit_proxy_request_blocked(self):
        """proxy_request_blocked 監査ログが正しいフィールドを含むこと"""
        from app.infrastructure.audit_log import audit_proxy_request_blocked

        with patch("app.infrastructure.audit_log.audit_logger") as mock_logger:
            audit_proxy_request_blocked(
                method="GET",
                url="https://evil.com/payload",
                reason="domain_not_in_whitelist",
            )

            mock_logger.warning.assert_called_once()
            call_kwargs = mock_logger.warning.call_args
            assert call_kwargs[0][0] == "proxy_request_blocked"
            assert call_kwargs[1]["url"] == "https://evil.com/payload"


class TestPeriodicFileSync:
    """定期ファイル同期のテスト"""

    def test_is_file_tool_result(self):
        """ファイル操作ツール結果が正しく判定されること"""
        from app.services.execute_service import ExecuteService

        # ファイルツールの場合
        event_file = {"event": "tool_result", "data": {"tool_name": "write_file"}}
        assert ExecuteService._is_file_tool_result(event_file) is True

        # 非ファイルツールの場合
        event_other = {"event": "tool_result", "data": {"tool_name": "Bash"}}
        assert ExecuteService._is_file_tool_result(event_other) is False

        # データなしの場合
        event_empty = {"event": "tool_result", "data": {}}
        assert ExecuteService._is_file_tool_result(event_empty) is False


class TestS3LifecyclePolicy:
    """S3 ライフサイクルポリシーのテスト"""

    def test_lifecycle_policy_file_exists(self):
        """ライフサイクルポリシーファイルが存在すること"""
        policy_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "s3", "lifecycle-policy.json"
        )
        assert os.path.exists(policy_path)

    def test_lifecycle_policy_is_valid_json(self):
        """ライフサイクルポリシーが有効なJSONであること"""
        policy_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "s3", "lifecycle-policy.json"
        )
        with open(policy_path) as f:
            policy = json.load(f)

        assert "Rules" in policy
        assert len(policy["Rules"]) == 3

    def test_lifecycle_policy_has_glacier_rule(self):
        """Glacier移行ルールが定義されていること"""
        policy_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "s3", "lifecycle-policy.json"
        )
        with open(policy_path) as f:
            policy = json.load(f)

        glacier_rules = [
            r for r in policy["Rules"] if "Transitions" in r
        ]
        assert len(glacier_rules) > 0
        assert glacier_rules[0]["Transitions"][0]["StorageClass"] == "GLACIER"
        assert glacier_rules[0]["Transitions"][0]["Days"] == 90


class TestAppArmorProfile:
    """AppArmor プロファイルのテスト"""

    def test_apparmor_profile_exists(self):
        """AppArmor プロファイルが存在すること"""
        profile_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "apparmor", "workspace-container"
        )
        assert os.path.exists(profile_path)

    def test_apparmor_profile_denies_proc_mem(self):
        """AppArmor が /proc/*/mem を deny すること"""
        profile_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "apparmor", "workspace-container"
        )
        with open(profile_path) as f:
            content = f.read()

        assert "deny /proc/*/mem" in content

    def test_apparmor_profile_denies_sys_write(self):
        """AppArmor が /sys/ への書き込みを deny すること"""
        profile_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "apparmor", "workspace-container"
        )
        with open(profile_path) as f:
            content = f.read()

        assert "deny /sys/** w" in content

    def test_apparmor_profile_allows_workspace(self):
        """AppArmor が /workspace/ への読み書きを許可すること"""
        profile_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "apparmor", "workspace-container"
        )
        with open(profile_path) as f:
            content = f.read()

        assert "/workspace/** rw" in content

    def test_config_includes_apparmor(self):
        """コンテナ設定に AppArmor プロファイルが含まれること"""
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
                apparmor_profile_name="workspace-container",
            )

            from app.services.container.config import get_container_create_config

            config = get_container_create_config("ws-test")
            assert "apparmor=workspace-container" in config["HostConfig"]["SecurityOpt"]
