"""
Amazon Bedrock AgentCore Runtime クライアント

ContainerOrchestrator を完全代替。boto3 の bedrock-agentcore クライアントを使用し、
AgentCore Runtime の invoke_agent_runtime API を呼び出す。

API ドキュメント:
  - https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html
  - https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html
"""

import json
from collections.abc import AsyncIterator

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


class AgentCoreClient:
    """AgentCore Runtime クライアント

    invoke_agent_runtime API を使用して AgentCore Runtime にリクエストを送信し、
    SSE ストリーミングレスポンスを返す。
    """

    def __init__(
        self,
        runtime_arn: str | None = None,
        aws_region: str | None = None,
    ):
        settings = get_settings()
        self._runtime_arn = runtime_arn or settings.agentcore_runtime_arn
        self._aws_region = aws_region or settings.aws_region

        if not self._runtime_arn:
            logger.warning(
                "AGENTCORE_RUNTIME_ARN 未設定: AgentCore呼び出しは失敗します"
            )

    def _get_client(self):
        """boto3 bedrock-agentcore クライアントを取得"""
        import boto3
        from botocore.config import Config

        config = Config(
            region_name=self._aws_region,
            read_timeout=3600,  # 60分 SSEストリーミング対応
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        return boto3.client("bedrock-agentcore", config=config)

    async def invoke_streaming(
        self,
        payload: dict,
        session_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        """
        AgentCore Runtime にリクエストを送信し、SSEストリームを返す

        Args:
            payload: invocation ペイロード（JSON シリアライズされる）
            session_id: AgentCore セッションID（コンテナ親和性用）

        Yields:
            SSEストリームのバイトチャンク
        """
        import asyncio

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

            logger.info(
                "AgentCore invoke_agent_runtime 呼び出し",
                runtime_arn=self._runtime_arn,
                session_id=session_id,
                payload_size=len(kwargs["payload"]),
            )

            response = client.invoke_agent_runtime(**kwargs)
            return response

        # boto3 は同期APIのためスレッドプールで実行
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _invoke)

        # レスポンスからセッションIDを取得
        new_session_id = response.get("runtimeSessionId")
        if new_session_id:
            logger.info(
                "AgentCore セッションID取得",
                session_id=new_session_id,
            )

        # StreamingBody からチャンクを読み出し
        streaming_body = response.get("response") or response.get("body")
        if streaming_body is None:
            logger.error("AgentCore レスポンスにストリームなし")
            return

        # ストリーミングレスポンスをチャンクごとに yield
        # StreamingBody.iter_chunks() は同期イテレータのためスレッドで処理
        import asyncio

        def _read_chunks():
            """StreamingBody からチャンクを順次読み出すジェネレータ"""
            chunks = []
            try:
                for chunk in streaming_body.iter_chunks(chunk_size=4096):
                    chunks.append(chunk)
            except Exception as e:
                logger.error("AgentCore ストリーム読み出しエラー", error=str(e))
            finally:
                streaming_body.close()
            return chunks

        chunks = await loop.run_in_executor(None, _read_chunks)
        for chunk in chunks:
            yield chunk

    def extract_session_id(self, response: dict) -> str | None:
        """レスポンスから AgentCore セッションIDを抽出"""
        return response.get("runtimeSessionId")
