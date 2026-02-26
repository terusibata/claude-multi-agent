"""
セキュリティ監査ログ

構造化監査イベントを出力する。
全イベントに service, event, conversation_id, tenant_id を含める。
"""

import structlog

audit_logger = structlog.get_logger("audit")

SERVICE_EXECUTOR = "workspace-executor"


def audit_agent_execution_started(
    *,
    conversation_id: str,
    container_id: str,
    tenant_id: str = "",
    model_id: str = "",
) -> None:
    audit_logger.info(
        "agent_execution_started",
        service=SERVICE_EXECUTOR,
        conversation_id=conversation_id,
        container_id=container_id,
        tenant_id=tenant_id,
        model_id=model_id,
    )


def audit_agent_execution_completed(
    *,
    conversation_id: str,
    container_id: str,
    tenant_id: str = "",
    duration_ms: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: str = "0",
) -> None:
    audit_logger.info(
        "agent_execution_completed",
        service=SERVICE_EXECUTOR,
        conversation_id=conversation_id,
        container_id=container_id,
        tenant_id=tenant_id,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def audit_agent_execution_failed(
    *,
    conversation_id: str,
    container_id: str = "",
    tenant_id: str = "",
    error: str = "",
    error_type: str = "",
) -> None:
    audit_logger.error(
        "agent_execution_failed",
        service=SERVICE_EXECUTOR,
        conversation_id=conversation_id,
        container_id=container_id,
        tenant_id=tenant_id,
        error=error,
        error_type=error_type,
    )
