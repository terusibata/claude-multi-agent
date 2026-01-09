"""
AIエージェントバックエンド メインアプリケーション
AWS Bedrock + Claude Agent SDKを利用したマルチテナント対応AIエージェントシステム
"""
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import api_router
from app.config import get_settings
from app.database import close_db, init_db

# 設定読み込み
settings = get_settings()

# ログ設定
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    アプリケーションのライフサイクル管理
    起動時・終了時の処理を定義
    """
    # 起動時
    logger.info("アプリケーション起動中...", version=__version__)

    # AWS Bedrock環境変数の設定
    os.environ["CLAUDE_CODE_USE_BEDROCK"] = settings.claude_code_use_bedrock
    if settings.aws_region:
        os.environ["AWS_REGION"] = settings.aws_region
    if settings.aws_access_key_id:
        os.environ["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        os.environ["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key

    # データベース初期化は Alembic マイグレーションで実施
    # 開発環境でも本番環境でも、マイグレーションを使用することで一貫性を保つ
    # 起動前に `alembic upgrade head` を実行してください
    logger.info("データベースマイグレーションは alembic upgrade head で実行してください")

    logger.info("アプリケーション起動完了", environment=settings.app_env)

    yield

    # 終了時
    logger.info("アプリケーション終了中...")
    await close_db()
    logger.info("アプリケーション終了完了")


# FastAPIアプリケーション作成
app = FastAPI(
    title="AIエージェントバックエンド",
    description="""
## 概要

AWS Bedrock + Claude Agent SDKを利用したマルチテナント対応AIエージェントシステムのバックエンドAPIです。

## 主要機能

- **モデル管理**: 利用可能なAIモデルの定義と料金管理
- **エージェント設定**: テナントごとのエージェント実行設定
- **Agent Skills管理**: ファイルシステムベースのSkills管理
- **MCPサーバー管理**: Model Context Protocolサーバーの設定
- **エージェント実行**: ストリーミング対応のエージェント実行
- **セッション管理**: 会話履歴とセッションの管理
- **使用状況監視**: トークン使用量とコストのレポート

## 認証

認証・権限管理はフロントエンド側で実施されます。
APIへのアクセスはテナントID（tenant_id）単位で分離されます。
    """,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)

# CORSミドルウェア設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# エラーハンドラー
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """バリデーションエラーハンドラー"""
    logger.warning("バリデーションエラー", errors=exc.errors(), path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "入力データが不正です",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """一般エラーハンドラー"""
    logger.error("内部エラー", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "内部サーバーエラーが発生しました",
        },
    )


# ヘルスチェックエンドポイント
@app.get("/health", tags=["ヘルスチェック"])
async def health_check():
    """
    ヘルスチェック

    サーバーの稼働状態を確認します。
    """
    return {
        "status": "healthy",
        "version": __version__,
        "environment": settings.app_env,
    }


@app.get("/", tags=["ルート"])
async def root():
    """
    ルートエンドポイント

    APIの基本情報を返します。
    """
    return {
        "name": "AIエージェントバックエンド",
        "version": __version__,
        "docs_url": "/docs" if settings.is_development else None,
    }


# APIルーターを登録
app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=settings.is_development,
    )
