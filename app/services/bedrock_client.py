"""
Bedrock チャットクライアント
AWS Bedrock Converse API を直接呼び出すクライアント
"""
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

import structlog

from app.services.execute.aws_config import AWSConfig

logger = structlog.get_logger(__name__)


@dataclass
class StreamChunk:
    """ストリーミングチャンク"""

    type: str  # "text_delta" | "message_stop" | "metadata"
    content: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: Optional[str] = None


class BedrockChatClient:
    """
    AWS Bedrock Converse API を直接呼び出すクライアント

    Claude Agent SDK を使わず、純粋にBedrock APIでチャットを行う
    """

    def __init__(self, aws_config: AWSConfig):
        """
        初期化

        Args:
            aws_config: AWS設定
        """
        self.aws_config = aws_config

    def _format_messages(
        self,
        messages: list[dict],
    ) -> list[dict]:
        """
        メッセージをBedrock Converse API形式に変換

        Args:
            messages: [{"role": "user"|"assistant", "content": "..."}]

        Returns:
            Bedrock Converse API形式のメッセージリスト
        """
        formatted = []
        for msg in messages:
            formatted.append({
                "role": msg["role"],
                "content": [{"text": msg["content"]}],
            })
        return formatted

    async def stream_chat(
        self,
        model_id: str,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        ストリーミングでチャットを実行

        Args:
            model_id: Bedrock モデルID
            system_prompt: システムプロンプト
            messages: メッセージ履歴 [{"role": "user"|"assistant", "content": "..."}]
            max_tokens: 最大出力トークン数
            temperature: 温度パラメータ

        Yields:
            StreamChunk: ストリーミングチャンク
        """
        client = self.aws_config.create_bedrock_client()

        # リクエストパラメータ構築
        request_params = {
            "modelId": model_id,
            "messages": self._format_messages(messages),
            "system": [{"text": system_prompt}],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }

        logger.info(
            "Bedrock Converse Stream開始",
            model_id=model_id,
            message_count=len(messages),
        )

        try:
            # converse_stream APIを呼び出し
            response = client.converse_stream(**request_params)

            input_tokens = 0
            output_tokens = 0

            # ストリームを処理
            for event in response.get("stream", []):
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    if "text" in delta:
                        yield StreamChunk(
                            type="text_delta",
                            content=delta["text"],
                        )

                elif "messageStop" in event:
                    stop_reason = event["messageStop"].get("stopReason")
                    yield StreamChunk(
                        type="message_stop",
                        stop_reason=stop_reason,
                    )

                elif "metadata" in event:
                    usage = event["metadata"].get("usage", {})
                    input_tokens = usage.get("inputTokens", 0)
                    output_tokens = usage.get("outputTokens", 0)
                    yield StreamChunk(
                        type="metadata",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

            logger.info(
                "Bedrock Converse Stream完了",
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except Exception as e:
            logger.error(
                "Bedrock Converse Streamエラー",
                model_id=model_id,
                error=str(e),
            )
            raise

    def chat_sync(
        self,
        model_id: str,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> tuple[str, int, int]:
        """
        同期でチャットを実行（タイトル生成等に使用）

        Args:
            model_id: Bedrock モデルID
            system_prompt: システムプロンプト
            messages: メッセージ履歴
            max_tokens: 最大出力トークン数
            temperature: 温度パラメータ

        Returns:
            (応答テキスト, 入力トークン数, 出力トークン数)
        """
        client = self.aws_config.create_bedrock_client()

        request_params = {
            "modelId": model_id,
            "messages": self._format_messages(messages),
            "system": [{"text": system_prompt}],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }

        logger.info(
            "Bedrock Converse実行",
            model_id=model_id,
            message_count=len(messages),
        )

        response = client.converse(**request_params)

        # レスポンスからテキストを抽出
        content = response.get("output", {}).get("message", {}).get("content", [])
        text = ""
        for block in content:
            if "text" in block:
                text += block["text"]

        # 使用量を取得
        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        logger.info(
            "Bedrock Converse完了",
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return text, input_tokens, output_tokens


class SimpleChatTitleGenerator:
    """
    シンプルチャット用タイトル生成クラス
    """

    # タイトル生成用の軽量モデル
    DEFAULT_MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    MAX_TITLE_LENGTH = 50
    MAX_INPUT_LENGTH = 200
    MAX_RESPONSE_LENGTH = 300

    def __init__(self, bedrock_client: BedrockChatClient):
        """
        初期化

        Args:
            bedrock_client: Bedrockクライアント
        """
        self.bedrock_client = bedrock_client

    def generate(
        self,
        user_message: str,
        assistant_response: str,
    ) -> str:
        """
        会話からタイトルを生成

        Args:
            user_message: ユーザーメッセージ
            assistant_response: アシスタント応答

        Returns:
            生成されたタイトル
        """
        try:
            truncated_input = user_message[:self.MAX_INPUT_LENGTH]
            truncated_response = assistant_response[:self.MAX_RESPONSE_LENGTH]

            system_prompt = "あなたは会話のタイトルを生成するアシスタントです。簡潔で分かりやすいタイトルを生成してください。"

            prompt = f"""以下の会話から、短く簡潔な日本語のタイトルを生成してください。
タイトルは20文字以内にしてください。

ユーザー入力:
{truncated_input}

アシスタント応答:
{truncated_response}

タイトルのみを出力してください。説明は不要です。"""

            title, _, _ = self.bedrock_client.chat_sync(
                model_id=self.DEFAULT_MODEL_ID,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.7,
            )

            title = title.strip()

            # 最大文字数に制限
            if len(title) > self.MAX_TITLE_LENGTH:
                title = title[:self.MAX_TITLE_LENGTH]

            logger.info("タイトル生成成功", title=title)
            return title

        except Exception as e:
            logger.warning("タイトル生成失敗、フォールバック使用", error=str(e))
            return self._fallback_title(user_message)

    def _fallback_title(self, user_message: str) -> str:
        """フォールバックタイトルを生成"""
        if user_message:
            return user_message[:self.MAX_TITLE_LENGTH]
        return "新しいチャット"
