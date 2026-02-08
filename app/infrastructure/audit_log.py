"""
セキュリティ監査ログ (Phase 5)

仕様書記載のログフォーマットに準拠した構造化監査イベントを出力する。
全イベントに service, event, conversation_id, tenant_id, container_id を含める。

ログフォーマット例:
  {"timestamp":"2026-02-07T10:30:00Z","level":"INFO","service":"workspace-orchestrator",
   "event":"container_created","conversation_id":"conv-123","tenant_id":"tenant-456",
   "container_id":"ws-abc","source":"warm_pool","network_mode":"none","duration_ms":1200}
"""
import structlog

audit_logger = structlog.get_logger("audit")

SERVICE_ORCHESTRATOR = "workspace-orchestrator"
SERVICE_PROXY = "workspace-proxy"
SERVICE_FILE_SYNC = "workspace-file-sync"
SERVICE_EXECUTOR = "workspace-executor"


def audit_container_created(
    *,
    container_id: str,
    conversation_id: str,
    tenant_id: str = "",
    source: str = "warm_pool",
    duration_ms: int = 0,
) -> None:
    audit_logger.info(
        "container_created",
        service=SERVICE_ORCHESTRATOR,
        container_id=container_id,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        source=source,
        network_mode="none",
        duration_ms=duration_ms,
    )


def audit_container_destroyed(
    *,
    container_id: str,
    conversation_id: str = "",
    tenant_id: str = "",
    reason: str = "",
) -> None:
    audit_logger.info(
        "container_destroyed",
        service=SERVICE_ORCHESTRATOR,
        container_id=container_id,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        reason=reason,
    )


def audit_container_crashed(
    *,
    container_id: str,
    conversation_id: str = "",
    tenant_id: str = "",
    error: str = "",
) -> None:
    audit_logger.warning(
        "container_crashed",
        service=SERVICE_ORCHESTRATOR,
        container_id=container_id,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        error=error,
    )


def audit_proxy_request_allowed(
    *,
    method: str,
    url: str,
    container_id: str = "",
    status: int = 0,
    duration_ms: int = 0,
) -> None:
    audit_logger.info(
        "proxy_request_allowed",
        service=SERVICE_PROXY,
        container_id=container_id,
        method=method,
        url=url,
        status=status,
        duration_ms=duration_ms,
    )


def audit_proxy_request_blocked(
    *,
    method: str,
    url: str,
    container_id: str = "",
    reason: str = "domain_not_in_whitelist",
) -> None:
    audit_logger.warning(
        "proxy_request_blocked",
        service=SERVICE_PROXY,
        container_id=container_id,
        method=method,
        url=url,
        reason=reason,
    )


def audit_file_sync_to_container(
    *,
    conversation_id: str,
    container_id: str,
    tenant_id: str = "",
    synced_count: int = 0,
    total_count: int = 0,
) -> None:
    audit_logger.info(
        "file_sync_to_container",
        service=SERVICE_FILE_SYNC,
        conversation_id=conversation_id,
        container_id=container_id,
        tenant_id=tenant_id,
        synced_count=synced_count,
        total_count=total_count,
    )


def audit_file_sync_from_container(
    *,
    conversation_id: str,
    container_id: str,
    tenant_id: str = "",
    synced_count: int = 0,
) -> None:
    audit_logger.info(
        "file_sync_from_container",
        service=SERVICE_FILE_SYNC,
        conversation_id=conversation_id,
        container_id=container_id,
        tenant_id=tenant_id,
        synced_count=synced_count,
    )


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
