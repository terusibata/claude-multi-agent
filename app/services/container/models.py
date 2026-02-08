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
    agent_socket: str  # /var/run/ws/{id}/agent.sock
    proxy_socket: str  # /var/run/ws/{id}/proxy.sock
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: ContainerStatus = ContainerStatus.READY

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
        }

    @classmethod
    def from_redis_hash(cls, data: dict[str, str]) -> "ContainerInfo":
        """Redis Hashからデシリアライズ"""
        return cls(
            id=data["container_id"],
            conversation_id=data["conversation_id"],
            agent_socket=data["agent_socket"],
            proxy_socket=data["proxy_socket"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_active_at=datetime.fromisoformat(data["last_active_at"]),
            status=ContainerStatus(data["status"]),
        )

    def touch(self) -> None:
        """最終アクティブ時刻を更新"""
        self.last_active_at = datetime.now(timezone.utc)
