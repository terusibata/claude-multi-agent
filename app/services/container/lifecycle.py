"""
Docker コンテナマネージャー
Docker APIを使ったコンテナの作成・起動・停止・破棄を担当
"""
import asyncio
import os
import shutil
from pathlib import Path
from uuid import uuid4

import aiodocker
import httpx
import structlog

from app.config import get_settings
from app.services.container.base import ContainerManagerBase
from app.services.container.config import get_container_create_config
from app.services.container.models import ContainerInfo, ContainerStatus

logger = structlog.get_logger(__name__)


class DockerContainerManager(ContainerManagerBase):
    """Docker APIベースのコンテナマネージャー"""

    def __init__(self, docker: aiodocker.Docker) -> None:
        self.docker = docker
        self._settings = get_settings()

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
        socket_base = Path(self._settings.workspace_socket_base_path) / container_id
        socket_base.mkdir(parents=True, exist_ok=True)

        # ソケットディレクトリの権限設定
        # バックエンド (UID 1000) が作成 → ワークスペースコンテナも UID 1000 (config.py L89)
        # 同一UIDのため 0o755 で十分
        os.chmod(socket_base, 0o755)

        # ソケットパス: バックエンドコンテナ内から見たパス
        agent_socket = str(socket_base / "agent.sock")
        proxy_socket = str(socket_base / "proxy.sock")

        config = get_container_create_config(container_id)
        config["Labels"]["workspace.conversation_id"] = conversation_id
        # WarmPool用コンテナ（conversation_id未割当）にはラベルを付与し、GCの誤破棄を防止
        config["Labels"]["workspace.warm_pool"] = "true" if not conversation_id else "false"

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
            manager_type="docker",
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
        socket_dir = Path(self._settings.workspace_socket_base_path) / container_id
        if socket_dir.exists():
            shutil.rmtree(socket_dir, ignore_errors=True)

        logger.info("コンテナ破棄完了", container_id=container_id)

    async def is_healthy(
        self, container_id: str, check_agent: bool = False
    ) -> bool:
        """
        コンテナが健全かどうか確認

        Args:
            container_id: コンテナID
            check_agent: Trueの場合、Docker状態に加えてagent.sock経由で
                         workspace_agentプロセスの死活も確認する。

        Returns:
            True: 健全, False: 不健全
        """
        try:
            container = await self.docker.containers.get(container_id)
            info = await container.show()
            state = info.get("State", {})
            if not state.get("Running", False) or state.get("OOMKilled", False):
                return False
        except aiodocker.exceptions.DockerError:
            return False

        if not check_agent:
            return True

        # エージェントプロセスレベルのヘルスチェック
        agent_socket = str(
            Path(self._settings.workspace_socket_base_path) / container_id / "agent.sock"
        )
        try:
            transport = httpx.AsyncHTTPTransport(uds=agent_socket)
            async with httpx.AsyncClient(transport=transport, timeout=3.0) as client:
                resp = await client.get("http://localhost/health")
                return resp.status_code == 200
        except Exception:
            logger.warning(
                "エージェントヘルスチェック失敗",
                container_id=container_id,
            )
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

    async def wait_for_agent_ready(
        self, container_info: ContainerInfo, timeout: float = 30.0,
    ) -> bool:
        """
        agent.sock がリスン状態になるまでポーリング

        Args:
            container_info: コンテナ情報
            timeout: タイムアウト（秒）

        Returns:
            True: 準備完了, False: タイムアウト
        """
        agent_socket = container_info.agent_socket
        container_id = container_info.id

        deadline = asyncio.get_event_loop().time() + timeout
        poll_count = 0
        while asyncio.get_event_loop().time() < deadline:
            # コンテナの生存確認（5回に1回、即ち約2.5秒ごと）
            if container_id and poll_count % 5 == 0 and poll_count > 0:
                try:
                    container = await self.docker.containers.get(container_id)
                    info = await container.show()
                    state = info.get("State", {})
                    if not state.get("Running", False):
                        exit_code = state.get("ExitCode", -1)
                        container_logs = await self.get_container_logs(container_id)
                        logger.error(
                            "エージェントコンテナが早期終了",
                            container_id=container_id,
                            exit_code=exit_code,
                            agent_socket=agent_socket,
                            container_logs=container_logs,
                        )
                        return False
                except Exception:
                    pass

            try:
                transport = httpx.AsyncHTTPTransport(uds=agent_socket)
                async with httpx.AsyncClient(
                    transport=transport, timeout=2.0
                ) as client:
                    resp = await client.get("http://localhost/health")
                    if resp.status_code == 200:
                        logger.info(
                            "エージェント準備完了",
                            agent_socket=agent_socket,
                        )
                        return True
            except Exception:
                pass
            poll_count += 1
            await asyncio.sleep(0.5)

        # タイムアウト時にもコンテナログを取得
        container_logs = ""
        if container_id:
            container_logs = await self.get_container_logs(container_id)
        logger.error(
            "エージェント起動タイムアウト",
            agent_socket=agent_socket,
            timeout=timeout,
            container_id=container_id,
            container_logs=container_logs,
        )
        return False

    async def get_container_logs(self, container_id: str, tail: int = 80) -> str:
        """コンテナのログ末尾を取得（デバッグ用）"""
        try:
            container = await self.docker.containers.get(container_id)
            logs = await container.log(stdout=True, stderr=True, tail=tail)
            return "".join(logs) if logs else "<empty>"
        except Exception as e:
            return f"<log capture failed: {e}>"

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

    async def exec_in_container_binary(
        self, container_id: str, cmd: list[str]
    ) -> tuple[int, bytes]:
        """コンテナ内でコマンドを実行（バイナリ出力、stdoutのみ）

        get_archive が tmpfs マウント上のファイルを読めない問題の回避策として、
        exec + cat でコンテナ内プロセスからファイルを読み出す。
        """
        container = await self.docker.containers.get(container_id)
        exec_instance = await container.exec(cmd=cmd)

        stdout_chunks = []
        async with exec_instance.start() as stream:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    break
                # stream == 1: stdout, stream == 2: stderr
                if msg.stream == 1:
                    stdout_chunks.append(msg.data)

        inspect = await exec_instance.inspect()
        exit_code = inspect.get("ExitCode", -1)
        return exit_code, b"".join(stdout_chunks)


# 後方互換エイリアス
ContainerLifecycleManager = DockerContainerManager
