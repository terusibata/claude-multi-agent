"""
Container Orchestrator
会話ごとのコンテナ管理を統括する中心モジュール

フロー:
  1. リクエスト受信
  2. Redis: conversation_id → container検索
     ├─ 存在 → TTLリセット → Unix Socket経由でリクエスト転送
     └─ なし → WarmPoolからコンテナ取得（空なら新規作成）
  3. conversation_idラベル付け
  4. Unix Socketペア作成 (agent.sock + proxy.sock)
  5. Credential Injection Proxy起動
  6. S3 → コンテナへファイル同期
  7. Redis記録 (TTL: 3600s)
  8. agent.sock経由で /execute POST
  9. SSEレスポンス中継
  10. 完了後、AI生成ファイルをS3同期
"""
import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
import structlog
from redis.asyncio import Redis

from app.config import get_settings
from app.infrastructure.audit_log import (
    audit_container_crashed,
    audit_container_created,
    audit_container_destroyed,
)
from app.infrastructure.metrics import (
    get_workspace_active_containers,
    get_workspace_container_crashes,
    get_workspace_container_startup,
    get_workspace_requests_total,
)
from app.services.container.config import (
    CONTAINER_TTL_SECONDS,
    REDIS_KEY_CONTAINER,
    REDIS_KEY_CONTAINER_REVERSE,
)
from app.services.container.lifecycle import ContainerLifecycleManager
from app.services.container.models import ContainerInfo, ContainerStatus
from app.services.container.warm_pool import WarmPoolManager
from app.services.proxy.credential_proxy import CredentialInjectionProxy, ProxyConfig
from app.services.proxy.sigv4 import AWSCredentials

logger = structlog.get_logger(__name__)


class ContainerOrchestrator:
    """コンテナオーケストレーター"""

    def __init__(
        self,
        lifecycle: ContainerLifecycleManager,
        warm_pool: WarmPoolManager,
        redis: Redis,
    ) -> None:
        self.lifecycle = lifecycle
        self.warm_pool = warm_pool
        self.redis = redis
        self._proxies: dict[str, CredentialInjectionProxy] = {}
        self._settings = get_settings()

    async def get_or_create(self, conversation_id: str) -> ContainerInfo:
        """
        会話に対応するコンテナを取得または作成

        Args:
            conversation_id: 会話ID

        Returns:
            コンテナ情報
        """
        # Redis から既存コンテナを検索
        existing = await self._get_container_from_redis(conversation_id)
        if existing and await self.lifecycle.is_healthy(existing.id):
            existing.touch()
            await self._update_redis(existing)
            logger.info(
                "既存コンテナ再利用",
                container_id=existing.id,
                conversation_id=conversation_id,
            )
            return existing

        # 既存が不健全な場合はクリーンアップ
        if existing:
            logger.warning(
                "不健全コンテナ検出、再作成",
                container_id=existing.id,
                conversation_id=conversation_id,
            )
            await self._cleanup_container(existing)

        # WarmPoolからコンテナ取得
        startup_start = time.perf_counter()
        info = await self.warm_pool.acquire()
        info.conversation_id = conversation_id
        info.status = ContainerStatus.READY
        info.touch()

        # Proxy起動
        await self._start_proxy(info)

        # Redis に記録
        await self._save_to_redis(info)

        # メトリクス: コンテナ起動時間 + アクティブコンテナ数
        startup_duration = time.perf_counter() - startup_start
        get_workspace_container_startup().observe(startup_duration)
        get_workspace_active_containers().inc()

        logger.info(
            "コンテナ割り当て完了",
            container_id=info.id,
            conversation_id=conversation_id,
            startup_seconds=round(startup_duration, 3),
        )
        audit_container_created(
            container_id=info.id,
            conversation_id=conversation_id,
            source="warm_pool",
            duration_ms=int(startup_duration * 1000),
        )
        return info

    async def execute(
        self,
        conversation_id: str,
        request_body: dict,
        container_info: ContainerInfo | None = None,
    ) -> AsyncIterator[bytes]:
        """
        コンテナ内のエージェントにリクエストを転送し、SSEストリームを中継

        コンテナクラッシュ時は自動復旧を試み、container_recovered イベントを通知する。

        Args:
            conversation_id: 会話ID
            request_body: ExecuteRequest のJSON dict
            container_info: 事前に取得済みのコンテナ情報（省略時は内部で取得）

        Yields:
            SSEイベントのバイト列
        """
        info = container_info or await self.get_or_create(conversation_id)
        recovered = False

        # ステータスを running に更新
        info.status = ContainerStatus.RUNNING
        info.touch()
        await self._update_redis(info)

        agent_socket = info.agent_socket

        try:
            transport = httpx.AsyncHTTPTransport(uds=agent_socket)
            async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(self._settings.container_execution_timeout, connect=30.0)) as client:
                async with client.stream(
                    "POST",
                    "http://localhost/execute",
                    json=request_body,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk

        except httpx.TimeoutException:
            get_workspace_requests_total().inc(status="timeout")
            logger.error(
                "コンテナ実行タイムアウト",
                container_id=info.id,
                conversation_id=conversation_id,
            )
            yield b"event: error\ndata: {\"message\": \"Execution timeout\"}\n\n"
            # タイムアウト後もコンテナ内エージェントが実行中の可能性があるため、
            # コンテナを破棄して次回リクエスト用に新規作成する
            try:
                old_container_id = info.id
                await self._cleanup_container(info)
                new_info = await self.get_or_create(conversation_id)
                info = new_info
                recovered = True
                logger.info(
                    "タイムアウトコンテナ復旧完了",
                    old_container_id=old_container_id,
                    new_container_id=new_info.id,
                    conversation_id=conversation_id,
                )
            except Exception as cleanup_err:
                logger.error("タイムアウトコンテナクリーンアップ失敗", error=str(cleanup_err))
        except ConnectionError as e:
            # Proxy接続エラー: まずProxy単体の再起動を試行（Step 3-3）
            get_workspace_requests_total().inc(status="error")
            logger.warning(
                "Proxy接続エラー検出、Proxy再起動試行",
                container_id=info.id,
                conversation_id=conversation_id,
                error=str(e),
            )
            yield b"event: error\ndata: {\"message\": \"Container execution failed\"}\n\n"
            try:
                await self._restart_proxy(info)
                yield b'event: container_recovered\ndata: {"message": "Container recovered", "recovered": true, "retry_recommended": true}\n\n'
            except Exception as proxy_err:
                logger.error("Proxy再起動失敗、コンテナ全体復旧へ", error=str(proxy_err))
                get_workspace_container_crashes().inc()
                audit_container_crashed(
                    container_id=info.id,
                    conversation_id=conversation_id,
                    error=str(e),
                )
                try:
                    old_container_id = info.id
                    await self._cleanup_container(info)
                    new_info = await self.get_or_create(conversation_id)
                    info = new_info
                    recovered = True
                    logger.info(
                        "コンテナ復旧完了",
                        old_container_id=old_container_id,
                        new_container_id=new_info.id,
                        conversation_id=conversation_id,
                    )
                    yield b'event: container_recovered\ndata: {"message": "Container recovered", "recovered": true, "retry_recommended": true}\n\n'
                except Exception as recovery_err:
                    logger.error("コンテナ復旧失敗", error=str(recovery_err))

        except Exception as e:
            get_workspace_requests_total().inc(status="error")
            get_workspace_container_crashes().inc()
            logger.error(
                "コンテナ実行エラー",
                container_id=info.id,
                conversation_id=conversation_id,
                error=str(e),
            )
            audit_container_crashed(
                container_id=info.id,
                conversation_id=conversation_id,
                error=str(e),
            )
            yield b"event: error\ndata: {\"message\": \"Container execution failed\"}\n\n"

            # クラッシュ復旧: 不健全コンテナをクリーンアップし新コンテナを準備
            try:
                old_container_id = info.id
                await self._cleanup_container(info)
                new_info = await self.get_or_create(conversation_id)
                info = new_info
                recovered = True
                logger.info(
                    "コンテナ復旧完了",
                    old_container_id=old_container_id,
                    new_container_id=new_info.id,
                    conversation_id=conversation_id,
                )
                yield b'event: container_recovered\ndata: {"message": "Container recovered", "recovered": true, "retry_recommended": true}\n\n'
            except Exception as recovery_err:
                logger.error("コンテナ復旧失敗", error=str(recovery_err))
        else:
            get_workspace_requests_total().inc(status="success")
        finally:
            # 復旧済みの場合はget_or_createが既にRedisを更新済みなのでスキップ
            if not recovered:
                info.status = ContainerStatus.IDLE
                info.touch()
                await self._update_redis(info)

    async def destroy(self, conversation_id: str) -> None:
        """会話に紐づくコンテナを破棄"""
        info = await self._get_container_from_redis(conversation_id)
        if not info:
            return

        await self._cleanup_container(info)
        logger.info("コンテナ破棄完了", conversation_id=conversation_id)

    async def destroy_all(self) -> None:
        """全コンテナを破棄（シャットダウン時）"""
        logger.info("全コンテナ破棄開始")

        # 全Proxyを先に停止
        proxy_ids = list(self._proxies.keys())
        for proxy_id in proxy_ids:
            try:
                await self._stop_proxy(proxy_id)
            except Exception as e:
                logger.warning("Proxy停止エラー", container_id=proxy_id, error=str(e))

        # 全コンテナを破棄
        containers = await self.lifecycle.list_workspace_containers()
        tasks = []
        for c in containers:
            container_name = c.get("Name", "").lstrip("/")
            if container_name:
                tasks.append(self.lifecycle.destroy_container(container_name, grace_period=5))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # WarmPoolもドレイン
        await self.warm_pool.drain()
        logger.info("全コンテナ破棄完了", count=len(tasks))

    # ---- Private methods ----

    async def _start_proxy(self, info: ContainerInfo) -> None:
        """コンテナ用Proxyを起動"""
        aws_creds = AWSCredentials(
            access_key_id=self._settings.aws_access_key_id or "",
            secret_access_key=self._settings.aws_secret_access_key or "",
            session_token=self._settings.aws_session_token,
            region=self._settings.aws_region,
        )
        proxy_config = ProxyConfig(
            whitelist_domains=self._settings.proxy_domain_whitelist_list,
            aws_credentials=aws_creds,
            log_all_requests=self._settings.proxy_log_all_requests,
        )
        proxy = CredentialInjectionProxy(proxy_config, info.proxy_socket)
        await proxy.start()
        self._proxies[info.id] = proxy

        # Proxyソケットの接続可能性を検証
        await self._verify_proxy_ready(info.proxy_socket, info.id)

    async def _verify_proxy_ready(
        self, proxy_socket: str, container_id: str, timeout: float = 5.0
    ) -> None:
        """
        Proxyソケットに接続可能か検証する。

        start()直後にソケットが実際にacceptできる状態かを確認。
        接続テスト失敗時はエラーログを出力するが、処理は続行する
        （後続のSDK初期化で再試行されるため）。
        """
        import os
        try:
            if not os.path.exists(proxy_socket):
                logger.error(
                    "Proxy検証: ソケットファイルが存在しません",
                    proxy_socket=proxy_socket,
                    container_id=container_id,
                )
                return

            stat = os.stat(proxy_socket)
            mode = oct(stat.st_mode)[-3:]
            logger.info(
                "Proxy検証: ソケット情報",
                proxy_socket=proxy_socket,
                permissions=mode,
                uid=stat.st_uid,
                gid=stat.st_gid,
                container_id=container_id,
            )

            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(proxy_socket),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            logger.info(
                "Proxy検証: 接続テスト成功",
                proxy_socket=proxy_socket,
                container_id=container_id,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Proxy検証: 接続テストタイムアウト",
                proxy_socket=proxy_socket,
                container_id=container_id,
                timeout=timeout,
            )
        except Exception as e:
            logger.error(
                "Proxy検証: 接続テスト失敗",
                proxy_socket=proxy_socket,
                container_id=container_id,
                error=str(e),
            )

    async def _stop_proxy(self, container_id: str) -> None:
        """コンテナ用Proxyを停止"""
        proxy = self._proxies.pop(container_id, None)
        if proxy:
            await proxy.stop()

    async def _restart_proxy(self, info: ContainerInfo) -> None:
        """Proxyクラッシュ時の自動再起動"""
        logger.warning("Proxy再起動", container_id=info.id)
        await self._stop_proxy(info.id)
        await self._start_proxy(info)

    async def _get_container_from_redis(self, conversation_id: str) -> ContainerInfo | None:
        """Redisからコンテナ情報を取得"""
        data = await self.redis.hgetall(f"{REDIS_KEY_CONTAINER}:{conversation_id}")
        if not data:
            return None
        return ContainerInfo.from_redis_hash(data)

    async def _save_to_redis(self, info: ContainerInfo) -> None:
        """コンテナ情報をRedisに保存"""
        key = f"{REDIS_KEY_CONTAINER}:{info.conversation_id}"
        await self.redis.hset(key, mapping=info.to_redis_hash())
        await self.redis.expire(key, CONTAINER_TTL_SECONDS)
        # 逆引きマッピング: container_id → conversation_id（GCが正しくコンテナを識別するため）
        reverse_key = f"{REDIS_KEY_CONTAINER_REVERSE}:{info.id}"
        await self.redis.set(reverse_key, info.conversation_id, ex=CONTAINER_TTL_SECONDS)

    async def _update_redis(self, info: ContainerInfo) -> None:
        """コンテナ情報をRedisで更新（TTLリセット含む）"""
        key = f"{REDIS_KEY_CONTAINER}:{info.conversation_id}"
        await self.redis.hset(key, mapping={
            "last_active_at": info.last_active_at.isoformat(),
            "status": info.status.value,
        })
        await self.redis.expire(key, CONTAINER_TTL_SECONDS)
        # 逆引きマッピングのTTLもリセット
        reverse_key = f"{REDIS_KEY_CONTAINER_REVERSE}:{info.id}"
        await self.redis.expire(reverse_key, CONTAINER_TTL_SECONDS)

    async def _cleanup_container(self, info: ContainerInfo) -> None:
        """コンテナとProxy、Redisメタデータをクリーンアップ"""
        await self._stop_proxy(info.id)
        try:
            await self.lifecycle.destroy_container(
                info.id, grace_period=self._settings.container_grace_period
            )
        except Exception as e:
            logger.error("コンテナ破棄エラー", container_id=info.id, error=str(e))
        await self.redis.delete(f"{REDIS_KEY_CONTAINER}:{info.conversation_id}")
        await self.redis.delete(f"{REDIS_KEY_CONTAINER_REVERSE}:{info.id}")
        get_workspace_active_containers().dec()
        audit_container_destroyed(
            container_id=info.id,
            conversation_id=info.conversation_id,
            reason="cleanup",
        )
