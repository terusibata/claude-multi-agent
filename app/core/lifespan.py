"""
アプリケーションライフサイクル管理
起動時・終了時の処理を定義

AgentCore Runtime 版: エージェント実行は AgentCore に委任。
"""
import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import get_settings
from app.database import close_db
from app.infrastructure.shutdown import get_shutdown_manager
from app.services.agentcore_client import AgentCoreClient

logger = structlog.get_logger(__name__)


async def _recover_skills_from_s3(settings) -> None:
    """
    起動時にローカルのスキルディレクトリが空の場合、S3から復元する。

    正常な再起動（volume維持）ではスキップされる。
    volume消失後の再起動でのみ実行される。
    """
    if not settings.s3_skills_backup_enabled:
        logger.info("S3スキルバックアップ無効（復元スキップ）")
        return

    if not settings.s3_bucket_name:
        logger.debug("S3バケット未設定（スキル復元スキップ）")
        return

    from pathlib import Path

    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models.agent_skill import AgentSkill
    from app.services.skill_s3_backup import SkillS3Backup

    try:
        # DBからスキルが存在するテナントIDを取得
        async with async_session_maker() as db:
            result = await db.execute(
                select(AgentSkill.tenant_id).distinct()
            )
            tenant_ids = [row[0] for row in result.all()]

        if not tenant_ids:
            logger.debug("スキルレコードなし（復元スキップ）")
            return

        base_path = Path(settings.skills_base_path)
        backup = SkillS3Backup()
        total_restored = 0

        for tenant_id in tenant_ids:
            tenant_skills_path = (
                base_path / f"tenant_{tenant_id}" / ".claude" / "skills"
            )

            # ローカルにファイルが存在する場合はスキップ
            has_local_files = (
                tenant_skills_path.exists()
                and any(tenant_skills_path.rglob("*"))
            )
            if has_local_files:
                logger.debug(
                    "ローカルスキルあり（復元スキップ）",
                    tenant_id=tenant_id,
                )
                continue

            # S3から復元
            logger.info(
                "S3からスキル復元開始",
                tenant_id=tenant_id,
            )
            restored = await backup.restore_tenant_skills(
                tenant_id, base_path
            )
            total_restored += restored

        if total_restored > 0:
            logger.info(
                "S3スキル復元完了",
                total_restored=total_restored,
                tenant_count=len(tenant_ids),
            )

    except Exception as e:
        logger.error(
            "S3スキル復元エラー（起動は継続）",
            error=str(e),
        )


def _log_security_status(settings) -> None:
    """セキュリティ設定のログ出力"""
    if settings.api_keys_list:
        logger.info("API認証が有効化されています", key_count=len(settings.api_keys_list))
    else:
        logger.warning(
            "API認証が無効化されています",
            reason="API_KEYSが設定されていません",
        )

    if settings.metrics_enabled:
        logger.info("メトリクス収集が有効化されています")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    アプリケーションのライフサイクル管理

    AgentCore Runtime アーキテクチャ:
      - AgentCoreClient (boto3) で invoke_agent_runtime を呼び出し
      - エージェント実行は AgentCore Runtime に委任
    """
    from app import __version__

    settings = get_settings()
    shutdown_manager = get_shutdown_manager()

    logger.info(
        "アプリケーション起動中...",
        version=__version__,
        environment=settings.app_env,
    )

    # シグナルハンドラーを設定
    try:
        loop = asyncio.get_running_loop()
        shutdown_manager.setup_signal_handlers(loop)
    except Exception as e:
        logger.warning("シグナルハンドラー設定エラー", error=str(e))

    # AgentCore クライアント初期化
    agentcore_client = AgentCoreClient()
    app.state.agentcore_client = agentcore_client

    logger.info(
        "AgentCoreクライアント初期化完了",
        runtime_arn=settings.agentcore_runtime_arn or "(未設定)",
    )

    _log_security_status(settings)

    # S3からスキル復元（ローカルが空の場合のみ）
    await _recover_skills_from_s3(settings)

    logger.info(
        "アプリケーション起動完了",
        environment=settings.app_env,
        port=settings.app_port,
    )

    yield

    # ---- 終了時 ----
    logger.info("アプリケーション終了中...")

    await shutdown_manager.graceful_shutdown()

    try:
        await close_db()
    except Exception as e:
        logger.error("DBクローズエラー", error=str(e))

    logger.info("アプリケーション終了完了")
