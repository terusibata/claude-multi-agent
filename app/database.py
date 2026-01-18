"""
データベース接続管理
非同期PostgreSQL接続とセッション管理
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# 非同期エンジンの作成
# SQLログ出力: 開発環境かつDEBUGレベルの時のみ
_echo_sql = settings.is_development and settings.log_level.upper() == "DEBUG"
engine = create_async_engine(
    settings.database_url,
    echo=_echo_sql,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # 接続の有効性チェック
)

# セッションファクトリの作成
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """SQLAlchemyベースクラス"""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    データベースセッションを取得するDependency
    リクエストごとに新しいセッションを作成し、終了時にクローズ
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    データベースの初期化
    テーブルの作成（開発環境用）
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """
    データベース接続のクローズ
    アプリケーション終了時に呼び出す
    """
    await engine.dispose()
