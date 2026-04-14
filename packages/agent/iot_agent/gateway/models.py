from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..security.models import AccessScope, GatewayMode


class UpstreamConnectionState(StrEnum):
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    ENROLLING = "enrolling"
    CONNECTING = "connecting"
    ONLINE = "online"
    DEGRADED = "degraded"
    AUTH_FAILED = "auth_failed"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    RECOVERING = "recovering"


class GatewayInboundCommandState(StrEnum):
    RECEIVED = "received"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class GatewayOutboxState(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"


class UpstreamAuthMode(StrEnum):
    CONTROLLER = "controller"
    ZITADEL_SERVICE_ACCOUNT = "zitadel_service_account"


class UpstreamCertificateMode(StrEnum):
    NONE = "none"
    CONTROLLER = "controller"
    STEP_CA = "step_ca"


class UpstreamEdgeProvider(StrEnum):
    DIRECT = "direct"
    CADDY = "caddy"


class MutualTlsMode(StrEnum):
    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


class CertificateBootstrapMode(StrEnum):
    STEP_CA_OTT = "step_ca_ott"


class ManagedCertificateState(StrEnum):
    DISABLED = "disabled"
    WAITING_FOR_ENROLLMENT = "waiting_for_enrollment"
    WAITING_FOR_BOOTSTRAP = "waiting_for_bootstrap"
    VALID = "valid"
    RENEWAL_DUE = "renewal_due"
    BOOTSTRAPPING = "bootstrapping"
    RENEWING = "renewing"
    RENEWAL_FAILED = "renewal_failed"
    REBOOTSTRAP_REQUIRED = "rebootstrap_required"
    EXPIRED = "expired"


class ManagedCertificateOperation(StrEnum):
    IDLE = "idle"
    INSPECT = "inspect"
    BOOTSTRAP_ROOT = "bootstrap_root"
    ISSUE = "issue"
    RENEW = "renew"


class ManagedCertificateFailureReason(StrEnum):
    NONE = "none"
    NETWORK_ERROR = "network_error"
    AUTH_FAILED = "auth_failed"
    CA_UNAVAILABLE = "ca_unavailable"
    ROOT_FINGERPRINT_MISMATCH = "root_fingerprint_mismatch"
    LOCAL_CERTIFICATE_INVALID = "local_certificate_invalid"
    BOOTSTRAP_REQUIRED = "bootstrap_required"
    BOOTSTRAP_EXPIRED = "bootstrap_expired"
    RENEWAL_UNSUPPORTED = "renewal_unsupported"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class StepCaOttBootstrap:
    mode: CertificateBootstrapMode
    ca_url: str
    root_fingerprint: str
    ott: str | None = None
    sign_url: str | None = None
    renew_url: str | None = None
    expires_at: datetime | None = None
    subject: str | None = None
    authorized_sans: tuple[str, ...] = ()
    requires_mutual_tls_after_issuance: bool = False


@dataclass(slots=True, frozen=True)
class GatewayEnrollmentRecord:
    access_token: str | None
    enrolled_at: datetime
    expires_at: datetime | None = None
    refresh_token: str | None = None
    refresh_url: str | None = None
    status_url: str | None = None
    events_url: str | None = None
    granted_scopes: tuple[AccessScope, ...] = ()
    protocol_version: str | None = None
    controller_name: str | None = None
    controller_instance_id: str | None = None
    certificate_expires_at: datetime | None = None
    auth_mode: UpstreamAuthMode = UpstreamAuthMode.CONTROLLER
    certificate_mode: UpstreamCertificateMode = UpstreamCertificateMode.CONTROLLER
    edge_provider: UpstreamEdgeProvider = UpstreamEdgeProvider.DIRECT
    mutual_tls_mode: MutualTlsMode = MutualTlsMode.DISABLED
    certificate_bootstrap: StepCaOttBootstrap | None = None


@dataclass(slots=True, frozen=True)
class ManagedCertificateStatus:
    state: ManagedCertificateState
    operation: ManagedCertificateOperation = ManagedCertificateOperation.IDLE
    failure_reason: ManagedCertificateFailureReason = ManagedCertificateFailureReason.NONE
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


@dataclass(slots=True, frozen=True)
class UpstreamStatus:
    mode: GatewayMode
    state: UpstreamConnectionState
    base_url: str | None = None
    status_url: str | None = None
    events_url: str | None = None
    enrolled_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_event_at: datetime | None = None
    last_command_at: datetime | None = None
    last_command_id: str | None = None
    detail: str | None = None
    last_error: str | None = None
    protocol_version: str | None = None
    controller_name: str | None = None
    controller_instance_id: str | None = None
    auth_mode: UpstreamAuthMode = UpstreamAuthMode.CONTROLLER
    certificate_mode: UpstreamCertificateMode = UpstreamCertificateMode.CONTROLLER
    edge_provider: UpstreamEdgeProvider = UpstreamEdgeProvider.DIRECT
    mutual_tls_mode: MutualTlsMode = MutualTlsMode.DISABLED
    client_certificate_present: bool = False
    certificate_bootstrap_pending: bool = False
    retry_delay_seconds: float | None = None
    failed_sync_count: int = 0
    successful_sync_count: int = 0
    failed_event_stream_count: int = 0
    successful_event_stream_count: int = 0
    certificate_lifecycle: ManagedCertificateStatus | None = None


@dataclass(slots=True, frozen=True)
class GatewayInboundCommandRecord:
    command_id: str
    message_type: str
    state: GatewayInboundCommandState
    payload: dict[str, object]
    message_id: str
    received_at: datetime
    updated_at: datetime
    job_id: str | None = None
    response_payload: dict[str, object] | None = None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(slots=True, frozen=True)
class GatewayOutboxRecord:
    message_id: str
    message_type: str
    state: GatewayOutboxState
    payload: dict[str, object]
    created_at: datetime
    updated_at: datetime
    correlation_id: str | None = None
    dedupe_key: str | None = None
    sent_at: datetime | None = None
    acknowledged_at: datetime | None = None
    last_error: str | None = None
