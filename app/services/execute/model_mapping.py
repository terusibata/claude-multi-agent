"""
サブエージェントモデルマッピング
モデルエイリアス（haiku, sonnet等）と実際のモデルIDの対応を管理
"""
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)


class SubagentModelMapping:
    """
    サブエージェントモデルのエイリアスマッピング

    SDKで使用されるモデルエイリアス（haiku, sonnet, opus等）を
    実際のBedrock モデルIDに変換する
    """

    # エイリアス → Settings属性名のマッピング
    ALIAS_MAP = {
        "haiku": "anthropic_default_haiku_model",
        "sonnet": "anthropic_default_model",
        # "opus": 将来的に追加可能
    }

    @classmethod
    def resolve_model_id(cls, alias_or_id: str | None, settings: "Settings") -> str:
        """
        エイリアスを実際のモデルIDに解決

        Args:
            alias_or_id: モデルエイリアス（例: "haiku"）または直接のモデルID
            settings: アプリケーション設定

        Returns:
            解決されたモデルID（Bedrock形式）
        """
        if alias_or_id is None:
            # デフォルトはサブエージェント用モデル
            alias_or_id = settings.claude_code_subagent_model

        if alias_or_id in cls.ALIAS_MAP:
            attr_name = cls.ALIAS_MAP[alias_or_id]
            model_id = getattr(settings, attr_name)
            logger.debug(
                "モデルエイリアス解決",
                alias=alias_or_id,
                model_id=model_id,
            )
            return model_id

        # エイリアスでなければそのまま返す
        return alias_or_id

    @classmethod
    def get_required_model_ids(cls, settings: "Settings") -> set[str]:
        """
        起動時に検証が必要なモデルIDのセットを取得

        環境変数で設定されたすべてのモデルIDを返す

        Args:
            settings: アプリケーション設定

        Returns:
            検証が必要なモデルIDのセット
        """
        return {
            settings.anthropic_default_model,
            settings.anthropic_default_haiku_model,
        }

    @classmethod
    def is_valid_alias(cls, alias: str) -> bool:
        """
        有効なモデルエイリアスかどうかを確認

        Args:
            alias: チェックするエイリアス

        Returns:
            有効なエイリアスの場合はTrue
        """
        return alias in cls.ALIAS_MAP
