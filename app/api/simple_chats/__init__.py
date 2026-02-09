"""
シンプルチャットAPI
CRUD操作とストリーミング実行のエンドポイント
"""
from app.api.simple_chats.router import router as crud_router
from app.api.simple_chats.streaming import router as stream_router

__all__ = ["crud_router", "stream_router"]
