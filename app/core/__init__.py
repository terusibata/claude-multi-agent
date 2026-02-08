"""
コアモジュール
アプリケーションファクトリ、ライフサイクル管理、例外ハンドラーを提供
"""
from app.core.app_factory import create_app

__all__ = ["create_app"]
