"""
Amazon Bedrock AgentCore Runtime クライアント

boto3 の bedrock-agentcore クライアントを使用し、
AgentCore Runtime の invoke_agent_runtime API を呼び出す。

API ドキュメント:
  - https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html
  - https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html
"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

_SENTINEL = object()  # Queue終端マーカー


class AgentCoreClient:
    """AgentCore Runtime クライアント

    invoke_agent_runtime API を使用して AgentCore Runtime にリクエストを送信し、
    SSE ストリーミングレスポンスを返す。

    注意: このクラスはアプリケーション全体で共有されるシングルトンとして使用される。
    リクエスト固有の状態（セッションIDなど）はインスタンスに保持せず、
    呼び出し元から渡される metadata dict に格納する（並行リクエスト安全）。
    """

    def __init__(
        self,
        runtime_arn: str | None = None,
        aws_region: str | None = None,
        qualifier: str | None = None,
    ):
        settings = get_settings()
        self._runtime_arn = runtime_arn or settings.agentcore_runtime_arn
        self._aws_region = aws_region or settings.aws_region
        self._qualifier = qualifier or settings.agentcore_qualifier or None
        self._client = None
        self._client_lock = threading.Lock()

        if not self._runtime_arn:
            logger.warning(
                "AGENTCORE_RUNTIME_ARN 未設定: AgentCore呼び出しは失敗します"
            )

    def _get_client(self):
        """boto3 bedrock-agentcore クライアントを取得（スレッドセーフ・キャッシュ付き）"""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                import boto3
                from botocore.config import Config

                config = Config(
                    region_name=self._aws_region,
                    read_timeout=3600,  # 60分 SSEストリーミング対応
                    retries={"max_attempts": 3, "mode": "adaptive"},
                )
                self._client = boto3.client("bedrock-agentcore", config=config)
            return self._client

    async def invoke_streaming(
        self,
        payload: dict,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> AsyncIterator[str]:
        """
        AgentCore Runtime にリクエストを送信し、SSE行ストリームを返す

        Args:
            payload: invocation ペイロード（JSON シリアライズされる）
            session_id: AgentCore セッションID（コンテナ親和性用）
            metadata: 呼び出し元に返すメタデータ辞書（並行リクエスト安全）。
                      指定された場合、"agentcore_session_id" キーにセッションIDが格納される。

        Yields:
            SSEストリームの行文字列（改行なし、iter_lines()により改行は除去済み）
        """
        loop = asyncio.get_running_loop()

        def _invoke():
            client = self._get_client()

            kwargs = {
                "agentRuntimeArn": self._runtime_arn,
                "contentType": "application/json",
                "payload": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            }

            # セッション親和性: 既存セッションIDを渡すことで同一microVMにルーティング
            if session_id:
                kwargs["runtimeSessionId"] = session_id

            # バージョン/エンドポイント指定
            if self._qualifier:
                kwargs["qualifier"] = self._qualifier

            logger.info(
                "AgentCore invoke_agent_runtime 呼び出し",
                runtime_arn=self._runtime_arn,
                session_id=session_id,
                qualifier=self._qualifier,
                payload_size=len(kwargs["payload"]),
            )

            response = client.invoke_agent_runtime(**kwargs)
            return response

        # boto3 は同期APIのためスレッドプールで実行
        response = await loop.run_in_executor(None, _invoke)

        # レスポンスからセッションIDを取得し、呼び出し元のmetadataに格納
        new_session_id = response.get("runtimeSessionId")
        if new_session_id:
            if metadata is not None:
                metadata["agentcore_session_id"] = new_session_id
            logger.info(
                "AgentCore セッションID取得",
                session_id=new_session_id,
            )

        # StreamingBody からSSE行を読み出し
        # boto3 API レスポンスのストリーミングボディは "response" キーに格納される
        streaming_body = response.get("response")
        if streaming_body is None:
            logger.error(
                "AgentCore レスポンスにストリームなし",
                response_keys=list(response.keys()),
            )
            return

        # asyncio.Queue によるストリーミングブリッジ:
        # バックグラウンドスレッドで同期的にSSE行を読み出し、
        # Queue経由でasync側にリアルタイムで渡す
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)

        def _stream_to_queue():
            """StreamingBody からSSE行を読み出してQueueに投入"""
            try:
                # AWS公式推奨: SSEストリームには iter_lines() を使用
                for line in streaming_body.iter_lines():
                    decoded = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
                    asyncio.run_coroutine_threadsafe(
                        queue.put(decoded), loop
                    ).result(timeout=60)
            except Exception as e:
                logger.error("AgentCore ストリーム読み出しエラー", error=str(e))
            finally:
                streaming_body.close()
                asyncio.run_coroutine_threadsafe(
                    queue.put(_SENTINEL), loop
                ).result(timeout=10)

        # バックグラウンドスレッドで読み出し開始
        loop.run_in_executor(None, _stream_to_queue)

        # Queueから行をyield（真のストリーミング）
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item
