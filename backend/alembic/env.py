"""
Alembic環境設定
非同期PostgreSQL対応のマイグレーション環境
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# アプリケーションのモデルをインポート
from app.database import Base
from app.models import (
    AgentConfig,
    AgentSkill,
    ChatSession,
    DisplayCache,
    McpServer,
    MessageLog,
    Model,
    ToolExecutionLog,
    UsageLog,
)

# Alembic設定オブジェクト
config = context.config

# ログ設定
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# メタデータ（自動検出用）
target_metadata = Base.metadata


def get_url():
    """
    データベースURLを取得
    環境変数DATABASE_URLから取得し、なければalembic.iniの値を使用
    """
    import os
    url = os.environ.get("DATABASE_URL")
    if url:
        # asyncpgを使用するため、URLを調整
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif not url.startswith("postgresql+asyncpg://"):
            url = "postgresql+asyncpg://" + url.split("://", 1)[-1]
        return url
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """
    オフラインモードでマイグレーション実行
    'offline' modeでは、URLが設定されているが接続は確立しない
    SQLステートメントをスクリプトに出力する
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """
    マイグレーション実行（接続使用）
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    非同期マイグレーション実行
    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    オンラインモードでマイグレーション実行
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
