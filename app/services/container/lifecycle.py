"""
コンテナライフサイクル管理
Docker APIを使ったコンテナの作成・起動・停止・破棄を担当
"""
import asyncio
from pathlib import Path
from uuid import uuid4

import aiodocker
import structlog

from app.config import get_settings
from app.services.container.config import get_container_create_config
from app.services.container.models import ContainerInfo, ContainerStatus

logger = structlog.get_logger(__name__)
settings = get_settings()


class ContainerLifecycleManager:
    """コンテナの作成から破棄までを管理"""

    def __init__(self, docker: aiodocker.Docker) -> None:
        self.docker = docker

    async def create_container(self, conversation_id: str = "") -> ContainerInfo:
        """
        新しいワークスペースコンテナを作成・起動

        Args:
            conversation_id: 割り当て先会話ID（空文字ならWarmPool用）

        Returns:
            作成されたコンテナ情報
        """
        container_id = f"ws-{uuid4().hex[:12]}"

        # ソケットディレクトリを作成（バックエンドコンテナ内パス）
        # Bind mountはディレクトリ単位（BUG-06修正: ソケット競合状態回避）
        socket_base = Path(settings.workspace_socket_base_path) / container_id
        socket_base.mkdir(parents=True, exist_ok=True)

        # ソケットディレクトリの権限を設定
        # コンテナ内appuser (UID 1000) および userns-remap時のremappedユーザーがアクセスできるよう設定
        import os
        os.chmod(socket_base, 0o777)

        # ソケットパス: バックエンドコンテナ内から見たパス
        agent_socket = str(socket_base / "agent.sock")
        proxy_socket = str(socket_base / "proxy.sock")

        config = get_container_create_config(container_id)
        config["Labels"]["workspace.conversation_id"] = conversation_id

        logger.info(
            "コンテナ作成中",
            container_id=container_id,
            conversation_id=conversation_id,
            image=config["Image"],
        )

        container = await self.docker.containers.create_or_replace(
            name=container_id,
            config=config,
        )
        await container.start()

        info = ContainerInfo(
            id=container_id,
            conversation_id=conversation_id,
            agent_socket=agent_socket,
            proxy_socket=proxy_socket,
            status=ContainerStatus.WARM if not conversation_id else ContainerStatus.READY,
        )

        logger.info(
            "コンテナ起動完了",
            container_id=container_id,
            conversation_id=conversation_id,
        )
        return info

    async def destroy_container(self, container_id: str, grace_period: int = 30) -> None:
        """
        コンテナをグレースフルに破棄

        Args:
            container_id: コンテナID
            grace_period: 停止までの猶予秒数
        """
        logger.info("コンテナ破棄中", container_id=container_id, grace_period=grace_period)

        try:
            container = await self.docker.containers.get(container_id)
            await container.stop(t=grace_period)
            await container.delete(force=True)
        except aiodocker.exceptions.DockerError as e:
            if e.status == 404:
                logger.warning("コンテナ未検出（既に破棄済み）", container_id=container_id)
            else:
                logger.error("コンテナ破棄エラー", container_id=container_id, error=str(e))
                raise

        # ソケットディレクトリをクリーンアップ
        socket_dir = Path(settings.workspace_socket_base_path) / container_id
        if socket_dir.exists():
            import shutil
            shutil.rmtree(socket_dir, ignore_errors=True)

        logger.info("コンテナ破棄完了", container_id=container_id)

    async def is_healthy(self, container_id: str) -> bool:
        """コンテナが健全かどうか確認"""
        try:
            container = await self.docker.containers.get(container_id)
            info = await container.show()
            state = info.get("State", {})
            return state.get("Running", False) and not state.get("OOMKilled", False)
        except aiodocker.exceptions.DockerError:
            return False

    async def list_workspace_containers(self) -> list[dict]:
        """ワークスペースラベル付きの全コンテナを取得"""
        containers = await self.docker.containers.list(
            all=True,
            filters={"label": ["workspace=true"]},
        )
        result = []
        for c in containers:
            info = await c.show()
            result.append(info)
        return result

    async def exec_in_container(
        self, container_id: str, cmd: list[str]
    ) -> tuple[int, str]:
        """コンテナ内でコマンドを実行"""
        container = await self.docker.containers.get(container_id)
        exec_instance = await container.exec(cmd=cmd)

        output_chunks = []
        async with exec_instance.start() as stream:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    break
                output_chunks.append(msg.data.decode("utf-8", errors="replace"))

        inspect = await exec_instance.inspect()
        exit_code = inspect.get("ExitCode", -1)
        return exit_code, "".join(output_chunks)
