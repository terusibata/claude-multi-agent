"""
コンテナマネージャー抽象基底クラス
Docker / ECS 両実装の共通インターフェースを定義
"""
from abc import ABC, abstractmethod

from app.services.container.models import ContainerInfo


class ContainerManagerBase(ABC):
    """コンテナマネージャーの抽象基底クラス

    Docker (aiodocker) と ECS (RunTask) の両方を統一的に扱うためのインターフェース。
    """

    @abstractmethod
    async def create_container(self, conversation_id: str = "") -> ContainerInfo:
        """新しいワークスペースコンテナを作成・起動

        Args:
            conversation_id: 割り当て先会話ID（空文字ならWarmPool用）

        Returns:
            作成されたコンテナ情報
        """

    @abstractmethod
    async def destroy_container(self, container_id: str, grace_period: int = 30) -> None:
        """コンテナをグレースフルに破棄

        Args:
            container_id: コンテナID
            grace_period: 停止までの猶予秒数
        """

    @abstractmethod
    async def is_healthy(
        self, container_id: str, check_agent: bool = False
    ) -> bool:
        """コンテナが健全かどうか確認

        Args:
            container_id: コンテナID
            check_agent: Trueの場合、ランタイム状態に加えてagentプロセスの死活も確認

        Returns:
            True: 健全, False: 不健全
        """

    @abstractmethod
    async def list_workspace_containers(self) -> list[dict]:
        """ワークスペースラベル付きの全コンテナを取得"""

    @abstractmethod
    async def wait_for_agent_ready(
        self, container_info: ContainerInfo, timeout: float = 30.0,
    ) -> bool:
        """agentが起動完了するまでポーリング

        Args:
            container_info: コンテナ情報（ソケットパスまたはHTTP URL）
            timeout: タイムアウト（秒）

        Returns:
            True: 準備完了, False: タイムアウト
        """

    @abstractmethod
    async def exec_in_container(
        self, container_id: str, cmd: list[str]
    ) -> tuple[int, str]:
        """コンテナ内でコマンドを実行

        Returns:
            (exit_code, output)
        """

    @abstractmethod
    async def exec_in_container_binary(
        self, container_id: str, cmd: list[str]
    ) -> tuple[int, bytes]:
        """コンテナ内でコマンドを実行（バイナリ出力）

        Returns:
            (exit_code, stdout_bytes)
        """

    @abstractmethod
    async def get_container_logs(
        self, container_id: str, tail: int = 80
    ) -> str:
        """コンテナのログ末尾を取得（デバッグ用）"""
