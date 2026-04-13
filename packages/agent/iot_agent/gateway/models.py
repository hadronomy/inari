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
