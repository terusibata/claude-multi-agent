"""
ContainerInfo / ContainerStatus ユニットテスト

モデルのシリアライズ・デシリアライズと防御的パースをテストする。
"""
from datetime import datetime, timezone

import pytest

from app.services.container.models import ContainerInfo, ContainerStatus


def _make_redis_hash(**overrides):
    """テスト用のRedis Hash dictを生成"""
    now = datetime.now(timezone.utc)
    defaults = {
        "container_id": "ws-test-001",
        "conversation_id": "conv-001",
        "agent_socket": "/tmp/agent.sock",
        "proxy_socket": "/tmp/proxy.sock",
        "created_at": now.isoformat(),
        "last_active_at": now.isoformat(),
        "status": "ready",
        "task_arn": "",
        "task_ip": "",
        "manager_type": "docker",
    }
    defaults.update(overrides)
    return defaults


class TestParseStatus:
    """ContainerStatus パースの防御性テスト"""

    def test_valid_status(self):
        """正常なステータス値がパースされること"""
        for status in ContainerStatus:
            result = ContainerInfo._parse_status(status.value)
            assert result == status

    def test_invalid_status_defaults_to_ready(self):
        """不正なステータス値で READY にフォールバックすること"""
        result = ContainerInfo._parse_status("corrupted_value")
        assert result == ContainerStatus.READY

    def test_missing_status_defaults_to_ready(self):
        """None で READY にフォールバックすること"""
        result = ContainerInfo._parse_status(None)
        assert result == ContainerStatus.READY

    def test_empty_status_defaults_to_ready(self):
        """空文字列で READY にフォールバックすること"""
        result = ContainerInfo._parse_status("")
        assert result == ContainerStatus.READY


class TestFromRedisHash:
    """from_redis_hash のデシリアライズテスト"""

    def test_valid_hash(self):
        """正常なRedis Hashからの復元"""
        data = _make_redis_hash(status="running")
        info = ContainerInfo.from_redis_hash(data)
        assert info.id == "ws-test-001"
        assert info.status == ContainerStatus.RUNNING

    def test_invalid_status_in_hash(self):
        """Redis Hash に不正なステータスが入っていてもエラーにならないこと"""
        data = _make_redis_hash(status="unknown_status_xyz")
        info = ContainerInfo.from_redis_hash(data)
        assert info.status == ContainerStatus.READY

    def test_missing_status_in_hash(self):
        """Redis Hash に status キーがなくても復元できること"""
        data = _make_redis_hash()
        del data["status"]
        info = ContainerInfo.from_redis_hash(data)
        assert info.status == ContainerStatus.READY

    def test_roundtrip_serialization(self):
        """to_redis_hash → from_redis_hash の往復変換が正しいこと"""
        original = ContainerInfo(
            id="ws-round-001",
            conversation_id="conv-round-001",
            agent_socket="http://10.0.0.1:9000",
            proxy_socket="",
            status=ContainerStatus.IDLE,
            task_arn="arn:aws:ecs:us-west-2:123:task/cluster/abc",
            task_ip="10.0.0.1",
            manager_type="ecs",
        )
        data = original.to_redis_hash()
        restored = ContainerInfo.from_redis_hash(data)

        assert restored.id == original.id
        assert restored.conversation_id == original.conversation_id
        assert restored.agent_socket == original.agent_socket
        assert restored.status == original.status
        assert restored.task_arn == original.task_arn
        assert restored.task_ip == original.task_ip
        assert restored.manager_type == original.manager_type
