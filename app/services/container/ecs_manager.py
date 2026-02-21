"""
ECS コンテナマネージャー
AWS ECS RunTask APIを使ったコンテナの作成・起動・停止・破棄を担当

Docker (aiodocker) の代替実装。ECS Fargate/EC2 上のタスクとして
ワークスペースコンテナを管理する。

通信方式:
  - Docker: UDS (agent.sock / proxy.sock) 経由
  - ECS: HTTP (task_ip:9000) 経由。Proxyはサイドカーコンテナとして同一タスクで起動。
"""
import asyncio
from uuid import uuid4

import httpx
import structlog
from redis.asyncio import Redis

from app.config import get_settings
from app.services.container.base import ContainerManagerBase
from app.services.container.config import (
    REDIS_KEY_CONTAINER,
    REDIS_KEY_CONTAINER_REVERSE,
)
from app.services.container.models import ContainerInfo, ContainerStatus

logger = structlog.get_logger(__name__)

# ECSタスクのIPが取得できるまでのポーリング設定
_TASK_IP_POLL_INTERVAL = 2.0  # 秒
_TASK_IP_POLL_TIMEOUT = 120.0  # 秒


class EcsContainerManager(ContainerManagerBase):
    """ECS RunTask ベースのコンテナマネージャー"""

    def __init__(self, redis: Redis) -> None:
        self._settings = get_settings()
        self._redis = redis
        # aiobotocore セッション（lazy init）
        self._ecs_client = None
        self._logs_client = None
        self._session = None

    async def _get_ecs_client(self):
        """ECSクライアントをlazy初期化して返す"""
        if self._ecs_client is None:
            from aiobotocore.session import get_session
            self._session = get_session()
            ctx = self._session.create_client(
                "ecs",
                region_name=self._settings.aws_region,
            )
            self._ecs_client = await ctx.__aenter__()
            self._ecs_ctx = ctx
        return self._ecs_client

    async def _get_logs_client(self):
        """CloudWatch Logsクライアントをlazy初期化して返す"""
        if self._logs_client is None:
            from aiobotocore.session import get_session
            if self._session is None:
                self._session = get_session()
            ctx = self._session.create_client(
                "logs",
                region_name=self._settings.aws_region,
            )
            self._logs_client = await ctx.__aenter__()
            self._logs_ctx = ctx
        return self._logs_client

    async def close(self) -> None:
        """クライアントリソースをクリーンアップ"""
        if self._ecs_client:
            await self._ecs_ctx.__aexit__(None, None, None)
            self._ecs_client = None
        if self._logs_client:
            await self._logs_ctx.__aexit__(None, None, None)
            self._logs_client = None

    async def create_container(self, conversation_id: str = "") -> ContainerInfo:
        """ECS RunTaskでワークスペースタスクを起動"""
        container_id = f"ws-{uuid4().hex[:12]}"
        settings = self._settings

        ecs = await self._get_ecs_client()

        # RunTask パラメータ
        run_task_kwargs: dict = {
            "cluster": settings.ecs_cluster,
            "taskDefinition": settings.ecs_task_definition,
            "count": 1,
            "startedBy": f"backend/{container_id}",
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": settings.ecs_subnets_list,
                    "securityGroups": settings.ecs_security_groups_list,
                    "assignPublicIp": "DISABLED",
                },
            },
            "overrides": {
                "containerOverrides": [
                    {
                        "name": "workspace-agent",
                        "environment": [
                            {"name": "AGENT_LISTEN_MODE", "value": "http"},
                            {"name": "AGENT_HTTP_PORT", "value": str(settings.ecs_agent_port)},
                        ],
                    },
                ],
            },
            "tags": [
                {"key": "workspace", "value": "true"},
                {"key": "workspace.container_id", "value": container_id},
                {"key": "workspace.conversation_id", "value": conversation_id},
            ],
        }

        # Capacity Provider指定（EC2モードの場合）
        if settings.ecs_capacity_provider:
            run_task_kwargs["capacityProviderStrategy"] = [
                {
                    "capacityProvider": settings.ecs_capacity_provider,
                    "weight": 1,
                },
            ]

        logger.info(
            "ECSタスク起動中",
            container_id=container_id,
            conversation_id=conversation_id,
            cluster=settings.ecs_cluster,
        )

        response = await ecs.run_task(**run_task_kwargs)

        # failures チェック
        failures = response.get("failures", [])
        if failures:
            reasons = [f.get("reason", "unknown") for f in failures]
            raise RuntimeError(f"ECS RunTask failed: {reasons}")

        tasks = response.get("tasks", [])
        if not tasks:
            raise RuntimeError("ECS RunTask returned no tasks")

        task = tasks[0]
        task_arn = task["taskArn"]

        logger.info(
            "ECSタスク起動開始",
            container_id=container_id,
            task_arn=task_arn,
        )

        # タスクIPが取得できるまでポーリング
        task_ip = await self._wait_for_task_ip(task_arn)

        agent_url = f"http://{task_ip}:{settings.ecs_agent_port}"

        info = ContainerInfo(
            id=container_id,
            conversation_id=conversation_id,
            agent_socket=agent_url,  # ECSではHTTP URL
            proxy_socket="",  # サイドカーのため不要
            status=ContainerStatus.WARM if not conversation_id else ContainerStatus.READY,
            task_arn=task_arn,
            task_ip=task_ip,
            manager_type="ecs",
        )

        # Redis逆引き: container_id → task_arn（destroy時に使用）
        await self._redis.set(
            f"workspace:ecs_task:{container_id}",
            task_arn,
            ex=28800,  # 8時間
        )

        logger.info(
            "ECSタスク起動完了",
            container_id=container_id,
            task_arn=task_arn,
            task_ip=task_ip,
        )
        return info

    async def destroy_container(self, container_id: str, grace_period: int = 30) -> None:
        """ECSタスクを停止"""
        logger.info("ECSタスク停止中", container_id=container_id)

        task_arn = await self._resolve_task_arn(container_id)
        if not task_arn:
            logger.warning("ECSタスクARN未検出（既に破棄済み）", container_id=container_id)
            return

        ecs = await self._get_ecs_client()
        try:
            await ecs.stop_task(
                cluster=self._settings.ecs_cluster,
                task=task_arn,
                reason=f"Container {container_id} destroyed",
            )
        except Exception as e:
            # タスクが既に停止済みの場合はエラーを無視
            error_str = str(e)
            if "not found" in error_str.lower() or "InvalidParameterException" in error_str:
                logger.warning("ECSタスク未検出（既に停止済み）", container_id=container_id)
            else:
                logger.error("ECSタスク停止エラー", container_id=container_id, error=error_str)
                raise

        # Redis逆引きキーを削除
        await self._redis.delete(f"workspace:ecs_task:{container_id}")

        logger.info("ECSタスク停止完了", container_id=container_id)

    async def is_healthy(
        self, container_id: str, check_agent: bool = False
    ) -> bool:
        """ECSタスクが健全かどうか確認"""
        task_arn = await self._resolve_task_arn(container_id)
        if not task_arn:
            return False

        ecs = await self._get_ecs_client()
        try:
            response = await ecs.describe_tasks(
                cluster=self._settings.ecs_cluster,
                tasks=[task_arn],
            )
            tasks = response.get("tasks", [])
            if not tasks:
                return False

            task = tasks[0]
            last_status = task.get("lastStatus", "")
            if last_status != "RUNNING":
                return False
        except Exception:
            return False

        if not check_agent:
            return True

        # エージェントHTTPヘルスチェック
        task_ip = await self._get_task_ip(task_arn)
        if not task_ip:
            return False

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"http://{task_ip}:{self._settings.ecs_agent_port}/health"
                )
                return resp.status_code == 200
        except Exception:
            logger.warning(
                "ECSエージェントヘルスチェック失敗",
                container_id=container_id,
            )
            return False

    async def list_workspace_containers(self) -> list[dict]:
        """ECSクラスター上のワークスペースタスク一覧を取得"""
        ecs = await self._get_ecs_client()

        task_arns = []
        paginator_token = None

        # task_definition からfamily名を抽出
        # "workspace-agent" → "workspace-agent"
        # "workspace-agent:3" → "workspace-agent"
        # "arn:aws:ecs:...:task-definition/workspace-agent:3" → "workspace-agent"
        td = self._settings.ecs_task_definition
        family = td.split("/")[-1].split(":")[0]

        while True:
            kwargs: dict = {
                "cluster": self._settings.ecs_cluster,
                "family": family,
                "desiredStatus": "RUNNING",
            }
            if paginator_token:
                kwargs["nextToken"] = paginator_token

            response = await ecs.list_tasks(**kwargs)
            task_arns.extend(response.get("taskArns", []))

            paginator_token = response.get("nextToken")
            if not paginator_token:
                break

        if not task_arns:
            return []

        # バッチでDescribeTasks（最大100件ずつ）
        result = []
        for i in range(0, len(task_arns), 100):
            batch = task_arns[i:i + 100]
            response = await ecs.describe_tasks(
                cluster=self._settings.ecs_cluster,
                tasks=batch,
            )
            for task in response.get("tasks", []):
                # Docker互換のdict形式に変換
                tags = {t["key"]: t["value"] for t in task.get("tags", [])}
                container_id = tags.get("workspace.container_id", "")
                conversation_id = tags.get("workspace.conversation_id", "")

                result.append({
                    "Name": container_id,
                    "Created": task.get("createdAt", ""),
                    "Config": {
                        "Labels": {
                            "workspace": "true",
                            "workspace.container_id": container_id,
                            "workspace.conversation_id": conversation_id,
                        },
                    },
                    # ECS固有情報
                    "_ecs_task_arn": task["taskArn"],
                    "_ecs_last_status": task.get("lastStatus", ""),
                })

        return result

    async def wait_for_agent_ready(
        self, container_info: ContainerInfo, timeout: float = 30.0,
    ) -> bool:
        """HTTP /health エンドポイントでエージェント起動を待つ"""
        agent_url = container_info.agent_socket  # ECSではHTTP URL
        container_id = container_info.id
        task_arn = container_info.task_arn

        deadline = asyncio.get_event_loop().time() + timeout
        poll_count = 0

        while asyncio.get_event_loop().time() < deadline:
            # タスクの生存確認（5回に1回）
            if task_arn and poll_count % 5 == 0 and poll_count > 0:
                try:
                    ecs = await self._get_ecs_client()
                    response = await ecs.describe_tasks(
                        cluster=self._settings.ecs_cluster,
                        tasks=[task_arn],
                    )
                    tasks = response.get("tasks", [])
                    if tasks:
                        last_status = tasks[0].get("lastStatus", "")
                        if last_status in ("STOPPED", "DEPROVISIONING"):
                            stop_reason = tasks[0].get("stoppedReason", "unknown")
                            container_logs = await self.get_container_logs(container_id)
                            logger.error(
                                "ECSタスクが早期終了",
                                container_id=container_id,
                                task_arn=task_arn,
                                last_status=last_status,
                                stop_reason=stop_reason,
                                container_logs=container_logs,
                            )
                            return False
                except Exception:
                    pass

            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"{agent_url}/health")
                    if resp.status_code == 200:
                        logger.info(
                            "ECSエージェント準備完了",
                            container_id=container_id,
                            agent_url=agent_url,
                        )
                        return True
            except Exception:
                pass

            poll_count += 1
            await asyncio.sleep(0.5)

        # タイムアウト
        container_logs = await self.get_container_logs(container_id)
        logger.error(
            "ECSエージェント起動タイムアウト",
            container_id=container_id,
            agent_url=agent_url,
            timeout=timeout,
            container_logs=container_logs,
        )
        return False

    async def exec_in_container(
        self, container_id: str, cmd: list[str]
    ) -> tuple[int, str]:
        """HTTP /exec エンドポイント経由でコマンドを実行"""
        agent_url = await self._get_agent_url(container_id)
        if not agent_url:
            return -1, f"Container {container_id} not found"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{agent_url}/exec",
                    json={"cmd": cmd, "timeout": 60},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("exit_code", -1), data.get("output", "")
                return -1, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return -1, f"exec failed: {e}"

    async def exec_in_container_binary(
        self, container_id: str, cmd: list[str]
    ) -> tuple[int, bytes]:
        """HTTP /exec/binary エンドポイント経由でコマンドを実行（バイナリ出力）"""
        agent_url = await self._get_agent_url(container_id)
        if not agent_url:
            return -1, b""

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{agent_url}/exec/binary",
                    json={"cmd": cmd, "timeout": 60},
                )
                exit_code = int(resp.headers.get("X-Exit-Code", "-1"))
                return exit_code, resp.content
        except Exception as e:
            logger.error("exec_binary failed", container_id=container_id, error=str(e))
            return -1, b""

    async def get_container_logs(
        self, container_id: str, tail: int = 80
    ) -> str:
        """CloudWatch Logs APIからタスクログを取得"""
        task_arn = await self._resolve_task_arn(container_id)
        if not task_arn:
            return "<task not found>"

        # タスクIDを抽出（arn:aws:ecs:region:account:task/cluster/task-id → task-id）
        task_id = task_arn.split("/")[-1] if "/" in task_arn else task_arn

        try:
            logs_client = await self._get_logs_client()
            # ECS awslogsドライバーのロググループ/ストリーム命名規則:
            #   log_group: /ecs/{family}  (awslogs-group で設定)
            #   log_stream: {prefix}/{container-name}/{task-id}
            #     - prefix: タスク定義の awslogs-stream-prefix（通常 "ecs"）
            #     - container-name: タスク定義内のコンテナ名（"workspace-agent"）
            #     - task-id: ECSタスクUUID
            family = self._settings.ecs_task_definition.split("/")[-1].split(":")[0]
            log_group = f"/ecs/{family}"
            log_stream = f"ecs/workspace-agent/{task_id}"

            response = await logs_client.get_log_events(
                logGroupName=log_group,
                logStreamName=log_stream,
                limit=tail,
                startFromHead=False,
            )
            events = response.get("events", [])
            return "\n".join(e.get("message", "") for e in events) or "<empty>"
        except Exception as e:
            return f"<log capture failed: {e}>"

    # ---- Private helpers ----

    async def _wait_for_task_ip(self, task_arn: str) -> str:
        """タスクにENIが割り当てられてIPアドレスが取得できるまでポーリング"""
        ecs = await self._get_ecs_client()
        deadline = asyncio.get_event_loop().time() + _TASK_IP_POLL_TIMEOUT

        while asyncio.get_event_loop().time() < deadline:
            ip = await self._get_task_ip(task_arn)
            if ip:
                return ip

            # タスクが停止していないか確認
            response = await ecs.describe_tasks(
                cluster=self._settings.ecs_cluster,
                tasks=[task_arn],
            )
            tasks = response.get("tasks", [])
            if tasks:
                last_status = tasks[0].get("lastStatus", "")
                if last_status == "STOPPED":
                    stop_reason = tasks[0].get("stoppedReason", "unknown")
                    raise RuntimeError(
                        f"ECS task stopped before IP assignment: {stop_reason}"
                    )

            await asyncio.sleep(_TASK_IP_POLL_INTERVAL)

        raise RuntimeError(f"Timed out waiting for task IP: {task_arn}")

    async def _get_task_ip(self, task_arn: str) -> str | None:
        """タスクのプライベートIPを取得（未割当ならNone）"""
        ecs = await self._get_ecs_client()
        response = await ecs.describe_tasks(
            cluster=self._settings.ecs_cluster,
            tasks=[task_arn],
        )
        tasks = response.get("tasks", [])
        if not tasks:
            return None

        task = tasks[0]
        for attachment in task.get("attachments", []):
            if attachment.get("type") == "ElasticNetworkInterface":
                for detail in attachment.get("details", []):
                    if detail.get("name") == "privateIPv4Address":
                        return detail.get("value")
        return None

    async def _resolve_task_arn(self, container_id: str) -> str | None:
        """container_id → task_arn の逆引き（Redis）"""
        return await self._redis.get(f"workspace:ecs_task:{container_id}")

    async def _get_agent_url(self, container_id: str) -> str | None:
        """container_id → agent HTTP URL を取得"""
        # まずRedisのcontainer_reverseからconversation_idを引く
        conversation_id = await self._redis.get(
            f"{REDIS_KEY_CONTAINER_REVERSE}:{container_id}"
        )
        if conversation_id:
            data = await self._redis.hgetall(
                f"{REDIS_KEY_CONTAINER}:{conversation_id}"
            )
            if data:
                return data.get("agent_socket")

        # フォールバック: task_arn → describe_tasks → IP
        task_arn = await self._resolve_task_arn(container_id)
        if task_arn:
            task_ip = await self._get_task_ip(task_arn)
            if task_ip:
                return f"http://{task_ip}:{self._settings.ecs_agent_port}"

        return None
