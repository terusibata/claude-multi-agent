"""
データベース接続管理
非同期PostgreSQL接続とセッション管理
"""
from typing import AsyncGenerator

import structlog
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = structlog.get_logger(__name__)

settings = get_settings()

# 非同期エンジンの作成
# SQLログ出力: 開発環境かつDEBUGレベルの時のみ
_echo_sql = settings.is_development and settings.log_level.upper() == "DEBUG"

# asyncpg 接続引数
connect_args = {
    "timeout": settings.db_connect_timeout,
    "command_timeout": settings.db_command_timeout,
}

engine = create_async_engine(
    settings.database_url,
    echo=_echo_sql,
    # コネクションプール設定
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=True,  # 接続の有効性チェック
    # 接続引数
    connect_args=connect_args,
)


# コネクションプールのイベントリスナー（監視用）
@event.listens_for(engine.sync_engine.pool, "checkout")
def on_checkout(dbapi_connection, connection_record, connection_proxy):
    """接続がプールからチェックアウトされた時"""
    logger.debug(
        "DB接続チェックアウト",
        connection_id=id(dbapi_connection),
    )


@event.listens_for(engine.sync_engine.pool, "checkin")
def on_checkin(dbapi_connection, connection_record):
    """接続がプールに返却された時"""
    logger.debug(
        "DB接続チェックイン",
        connection_id=id(dbapi_connection),
    )


@event.listens_for(engine.sync_engine.pool, "connect")
def on_connect(dbapi_connection, connection_record):
    """新しい接続が作成された時"""
    logger.info(
        "DB接続作成",
        connection_id=id(dbapi_connection),
    )


@event.listens_for(engine.sync_engine.pool, "invalidate")
def on_invalidate(dbapi_connection, connection_record, exception):
    """接続が無効化された時"""
    logger.warning(
        "DB接続無効化",
        connection_id=id(dbapi_connection),
        error=str(exception) if exception else None,
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
    logger.info("データベース接続をクローズ中...")
    await engine.dispose()
    logger.info("データベース接続クローズ完了")


async def check_db_health() -> tuple[bool, str | None, float]:
    """
    データベースの接続状態をチェック

    Returns:
        (healthy: bool, error_message: str | None, latency_ms: float)
    """
    import time

    start = time.perf_counter()
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
        latency = (time.perf_counter() - start) * 1000
        return True, None, latency
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return False, str(e), latency


def get_pool_status() -> dict:
    """
    コネクションプールの状態を取得

    Returns:
        プール状態の辞書
    """
    pool = engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "invalid": pool.invalidatedcount if hasattr(pool, 'invalidatedcount') else 0,
    }
