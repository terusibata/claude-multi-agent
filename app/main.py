"""
AIエージェントバックエンド メインアプリケーション
コンテナ隔離型マルチテナント対応AIエージェントシステム
"""
from app.config import get_settings
from app.core.app_factory import create_app

# アプリケーション作成
app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=settings.is_development,
        workers=settings.uvicorn_workers if not settings.is_development else 1,
        timeout_keep_alive=settings.uvicorn_timeout_keep_alive,
        timeout_notify=settings.uvicorn_timeout_notify,
    )
