from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import APIModel
from ...gateway.models import (
    ManagedCertificateFailureReason,
    ManagedCertificateOperation,
    ManagedCertificateState,
    ManagedCertificateStatus,
    MutualTlsMode,
    UpstreamCertificateMode,
    UpstreamConnectionState,
    UpstreamDataPlaneKind,
    UpstreamEdgeProvider,
    UpstreamStatus,
    ZenohSessionMode,
)
from ...security.models import AgentIdentity, GatewayExposure, GatewayMode


class GatewayIdentityResponse(APIModel):
    agent_id: str
    key_id: str
    algorithm: str
    public_jwk: dict[str, Any]
    created_at: datetime
    certificate_pem: str | None = None
    mode: GatewayMode
    exposure: GatewayExposure

    @classmethod
    def from_identity(
        cls,
        identity: AgentIdentity,
        *,
        mode: GatewayMode,
        exposure: GatewayExposure,
    ) -> GatewayIdentityResponse:
        return cls(
            agent_id=identity.agent_id,
            key_id=identity.key_id,
            algorithm=identity.algorithm,
            public_jwk=dict(identity.public_jwk),
            created_at=identity.created_at,
            certificate_pem=identity.certificate_pem,
            mode=mode,
            exposure=exposure,
        )


class ManagedCertificateStatusResponse(APIModel):
    state: ManagedCertificateState
    operation: ManagedCertificateOperation
    failure_reason: ManagedCertificateFailureReason
    detail: str | None = None
    current_expires_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_operation_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    next_action_at: datetime | None = None
    retry_delay_seconds: float | None = None
    certificate_present: bool = False
    bootstrap_pending: bool = False
    subject: str | None = None
    issuer: str | None = None
    serial_number: str | None = None
    successful_issue_count: int = 0
    failed_issue_count: int = 0
    successful_renewal_count: int = 0
    failed_renewal_count: int = 0

    @classmethod
    def from_status(
        cls, status: ManagedCertificateStatus
    ) -> ManagedCertificateStatusResponse:
        return cls(
            state=status.state,
            operation=status.operation,
            failure_reason=status.failure_reason,
            detail=status.detail,
            current_expires_at=status.current_expires_at,
            last_checked_at=status.last_checked_at,
            last_operation_at=status.last_operation_at,
            last_success_at=status.last_success_at,
            last_failure_at=status.last_failure_at,
            next_action_at=status.next_action_at,
            retry_delay_seconds=status.retry_delay_seconds,
            certificate_present=status.certificate_present,
            bootstrap_pending=status.bootstrap_pending,
            subject=status.subject,
            issuer=status.issuer,
            serial_number=status.serial_number,
            successful_issue_count=status.successful_issue_count,
            failed_issue_count=status.failed_issue_count,
            successful_renewal_count=status.successful_renewal_count,
            failed_renewal_count=status.failed_renewal_count,
        )


class GatewayUpstreamStatusResponse(APIModel):
    mode: GatewayMode
    state: UpstreamConnectionState
    base_url: str | None = None
    data_plane_kind: UpstreamDataPlaneKind | None = None
    data_plane_namespace: str | None = None
    data_plane_session_mode: ZenohSessionMode | None = None
    enrolled_at: datetime | None = None
    last_status_published_at: datetime | None = None
    last_data_plane_activity_at: datetime | None = None
    last_command_at: datetime | None = None
    last_command_id: str | None = None
    last_applied_controller_sequence: int | None = None
    detail: str | None = None
    last_error: str | None = None
    protocol_version: str | None = None
    controller_name: str | None = None
    controller_instance_id: str | None = None
    certificate_mode: UpstreamCertificateMode
    edge_provider: UpstreamEdgeProvider
    mutual_tls_mode: MutualTlsMode
    client_certificate_present: bool = False
    certificate_bootstrap_pending: bool = False
    retry_delay_seconds: float | None = None
    failed_status_publication_count: int = 0
    successful_status_publication_count: int = 0
    failed_data_plane_connection_count: int = 0
    successful_data_plane_connection_count: int = 0
    certificate_lifecycle: ManagedCertificateStatusResponse | None = None

    @classmethod
    def from_status(cls, status: UpstreamStatus) -> GatewayUpstreamStatusResponse:
        return cls(
            mode=status.mode,
            state=status.state,
            base_url=status.base_url,
            data_plane_kind=status.data_plane_kind,
            data_plane_namespace=status.data_plane_namespace,
            data_plane_session_mode=status.data_plane_session_mode,
            enrolled_at=status.enrolled_at,
            last_status_published_at=status.last_status_published_at,
            last_data_plane_activity_at=status.last_data_plane_activity_at,
            last_command_at=status.last_command_at,
            last_command_id=status.last_command_id,
            last_applied_controller_sequence=status.last_applied_controller_sequence,
            detail=status.detail,
            last_error=status.last_error,
            protocol_version=status.protocol_version,
            controller_name=status.controller_name,
            controller_instance_id=status.controller_instance_id,
            certificate_mode=status.certificate_mode,
            edge_provider=status.edge_provider,
            mutual_tls_mode=status.mutual_tls_mode,
            client_certificate_present=status.client_certificate_present,
            certificate_bootstrap_pending=status.certificate_bootstrap_pending,
            retry_delay_seconds=status.retry_delay_seconds,
            failed_status_publication_count=status.failed_status_publication_count,
            successful_status_publication_count=status.successful_status_publication_count,
            failed_data_plane_connection_count=status.failed_data_plane_connection_count,
            successful_data_plane_connection_count=status.successful_data_plane_connection_count,
            certificate_lifecycle=(
                ManagedCertificateStatusResponse.from_status(
                    status.certificate_lifecycle
                )
                if status.certificate_lifecycle is not None
                else None
            ),
        )
