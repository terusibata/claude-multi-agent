"""
コンテナ関連データモデル
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ContainerStatus(str, Enum):
    """コンテナの状態"""

    WARM = "warm"  # WarmPool待機中
    READY = "ready"  # 会話に割り当て済み、実行待ち
    RUNNING = "running"  # リクエスト実行中
    IDLE = "idle"  # 実行完了、アイドル状態
    DRAINING = "draining"  # シャットダウン中（新規リクエスト拒否）
    DESTROYED = "destroyed"  # 破棄済み


@dataclass
class ContainerInfo:
    """コンテナメタデータ"""

    id: str
    conversation_id: str
    agent_socket: str  # Docker: /var/run/ws/{id}/agent.sock, ECS: http://{task_ip}:9000
    proxy_socket: str  # Docker: /var/run/ws/{id}/proxy.sock, ECS: ""（サイドカー）
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: ContainerStatus = ContainerStatus.READY
    # ECS固有フィールド
    task_arn: str = ""
    task_ip: str = ""
    manager_type: str = "docker"  # "docker" or "ecs"

    def to_redis_hash(self) -> dict[str, str]:
        """Redis Hash用にシリアライズ"""
        return {
            "container_id": self.id,
            "conversation_id": self.conversation_id,
            "agent_socket": self.agent_socket,
            "proxy_socket": self.proxy_socket,
            "created_at": self.created_at.isoformat(),
            "last_active_at": self.last_active_at.isoformat(),
            "status": self.status.value,
            "task_arn": self.task_arn,
            "task_ip": self.task_ip,
            "manager_type": self.manager_type,
        }

    @classmethod
    def from_redis_hash(cls, data: dict[str, str]) -> "ContainerInfo":
        """Redis Hashからデシリアライズ（防御的: 新旧フィールド混在に対応）"""
        created_at_raw = data.get("created_at", "")
        last_active_raw = data.get("last_active_at", "")
        now = datetime.now(timezone.utc)

        return cls(
            id=data.get("container_id", ""),
            conversation_id=data.get("conversation_id", ""),
            agent_socket=data.get("agent_socket", ""),
            proxy_socket=data.get("proxy_socket", ""),
            created_at=datetime.fromisoformat(created_at_raw) if created_at_raw else now,
            last_active_at=datetime.fromisoformat(last_active_raw) if last_active_raw else now,
            status=ContainerStatus(data["status"]) if data.get("status") else ContainerStatus.READY,
            task_arn=data.get("task_arn", ""),
            task_ip=data.get("task_ip", ""),
            manager_type=data.get("manager_type", "docker"),
        )

    def touch(self) -> None:
        """最終アクティブ時刻を更新"""
        self.last_active_at = datetime.now(timezone.utc)
