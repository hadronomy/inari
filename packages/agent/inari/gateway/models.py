from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from ..security.models import GatewayMode


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


class UpstreamDataPlaneKind(StrEnum):
    ZENOH = "zenoh"


class ZenohSessionMode(StrEnum):
    CLIENT = "client"


class ZenohDataPlaneAuthKind(StrEnum):
    MTLS = "mtls"


class ZenohSerialization(StrEnum):
    JSON = "json"


class MutualTlsMode(StrEnum):
    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


class CertificateBootstrapAuthType(StrEnum):
    OTT = "ott"


class ControllerAction(StrEnum):
    SYSTEM_READ = "system:read"
    DEVICES_READ = "devices:read"
    EVENTS_READ = "events:read"
    JOBS_CREATE = "jobs:create"
    JOBS_CANCEL = "jobs:cancel"
    COMMANDS_EXECUTE = "commands:execute"


SUPPORTED_CONTROLLER_ACTIONS = (
    ControllerAction.SYSTEM_READ,
    ControllerAction.DEVICES_READ,
    ControllerAction.EVENTS_READ,
    ControllerAction.JOBS_CREATE,
    ControllerAction.JOBS_CANCEL,
    ControllerAction.COMMANDS_EXECUTE,
)


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


@dataclass(slots=True, frozen=True, kw_only=True)
class CertificateTrustSpec:
    root_fingerprint: str | None = None

    def to_persisted_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.root_fingerprint is not None:
            payload["root_fingerprint"] = self.root_fingerprint
        return payload


@dataclass(slots=True, frozen=True, kw_only=True)
class CertificateBootstrapAuth:
    type: CertificateBootstrapAuthType
    token: str | None = None
    expires_at: datetime | None = None

    def clear_token(self) -> CertificateBootstrapAuth:
        if self.token is None:
            return self
        return replace(self, token=None)

    def to_persisted_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"type": self.type}
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at
        return payload


@dataclass(slots=True, frozen=True, kw_only=True)
class CertificateEnrollmentSpec:
    base_url: str
    trust: CertificateTrustSpec | None = None
    bootstrap_auth: CertificateBootstrapAuth | None = None
    subject: str | None = None
    authorized_sans: tuple[str, ...] = ()
    requires_mutual_tls_after_issuance: bool = True

    @property
    def bootstrap_pending(self) -> bool:
        return bool(self.bootstrap_auth is not None and self.bootstrap_auth.token)

    def clear_bootstrap_token(self) -> CertificateEnrollmentSpec:
        if self.bootstrap_auth is None or self.bootstrap_auth.token is None:
            return self
        return replace(self, bootstrap_auth=self.bootstrap_auth.clear_token())

    def to_persisted_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "base_url": self.base_url,
            "authorized_sans": list(self.authorized_sans),
            "requires_mutual_tls_after_issuance": (
                self.requires_mutual_tls_after_issuance
            ),
        }
        if self.trust is not None:
            payload["trust"] = self.trust.to_persisted_dict()
        if self.bootstrap_auth is not None:
            payload["bootstrap_auth"] = self.bootstrap_auth.to_persisted_dict()
        if self.subject is not None:
            payload["subject"] = self.subject
        return payload


@dataclass(slots=True, frozen=True)
class MutualTlsPolicy:
    configured_mode: MutualTlsMode
    effective_mode: MutualTlsMode

    @property
    def requires_client_certificate(self) -> bool:
        return self.effective_mode is MutualTlsMode.REQUIRED

    @property
    def enabled(self) -> bool:
        return self.effective_mode is not MutualTlsMode.DISABLED


@dataclass(slots=True, frozen=True)
class ZenohDataPlaneConfig:
    kind: UpstreamDataPlaneKind
    session_mode: ZenohSessionMode
    connect_endpoints: tuple[str, ...]
    namespace: str
    serialization: ZenohSerialization = ZenohSerialization.JSON
    auth_kind: ZenohDataPlaneAuthKind = ZenohDataPlaneAuthKind.MTLS
    close_link_on_expiration: bool = True

    def to_persisted_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "session_mode": self.session_mode,
            "connect_endpoints": list(self.connect_endpoints),
            "namespace": self.namespace,
            "serialization": self.serialization,
            "auth": {"kind": self.auth_kind},
            "tls": {"close_link_on_expiration": self.close_link_on_expiration},
        }


@dataclass(slots=True, frozen=True)
class GatewayEnrollmentRecord:
    enrolled_at: datetime
    data_plane: ZenohDataPlaneConfig
    controller_actions: tuple[ControllerAction, ...] = ()
    protocol_version: str | None = None
    controller_name: str | None = None
    controller_instance_id: str | None = None
    certificate_expires_at: datetime | None = None
    certificate_mode: UpstreamCertificateMode = UpstreamCertificateMode.CONTROLLER
    edge_provider: UpstreamEdgeProvider = UpstreamEdgeProvider.DIRECT
    mutual_tls_mode: MutualTlsMode = MutualTlsMode.OPTIONAL
    certificate_enrollment: CertificateEnrollmentSpec | None = None

    @property
    def bootstrap_pending(self) -> bool:
        return bool(
            self.certificate_enrollment is not None
            and self.certificate_enrollment.bootstrap_pending
        )

    def clear_bootstrap_token(self) -> GatewayEnrollmentRecord:
        if self.certificate_enrollment is None:
            return self
        return replace(
            self,
            certificate_enrollment=self.certificate_enrollment.clear_bootstrap_token(),
        )

    def to_persisted_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "enrolled_at": self.enrolled_at,
            "data_plane": self.data_plane.to_persisted_dict(),
            "controller_actions": list(self.controller_actions),
            "protocol_version": self.protocol_version,
            "controller_name": self.controller_name,
            "controller_instance_id": self.controller_instance_id,
            "certificate_expires_at": self.certificate_expires_at,
            "certificate_mode": self.certificate_mode,
            "edge_provider": self.edge_provider,
            "mutual_tls_mode": self.mutual_tls_mode,
        }
        if self.certificate_enrollment is not None:
            payload["certificate_enrollment"] = (
                self.certificate_enrollment.to_persisted_dict()
            )
        return payload


@dataclass(slots=True, frozen=True)
class ManagedCertificateStatus:
    state: ManagedCertificateState
    operation: ManagedCertificateOperation = ManagedCertificateOperation.IDLE
    failure_reason: ManagedCertificateFailureReason = (
        ManagedCertificateFailureReason.NONE
    )
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
    certificate_mode: UpstreamCertificateMode = UpstreamCertificateMode.CONTROLLER
    edge_provider: UpstreamEdgeProvider = UpstreamEdgeProvider.DIRECT
    mutual_tls_mode: MutualTlsMode = MutualTlsMode.OPTIONAL
    client_certificate_present: bool = False
    certificate_bootstrap_pending: bool = False
    retry_delay_seconds: float | None = None
    failed_status_publication_count: int = 0
    successful_status_publication_count: int = 0
    failed_data_plane_connection_count: int = 0
    successful_data_plane_connection_count: int = 0
    certificate_lifecycle: ManagedCertificateStatus | None = None


@dataclass(slots=True, frozen=True)
class GatewayInboundCommandRecord:
    command_id: str
    message_type: str
    state: GatewayInboundCommandState
    payload: dict[str, object]
    message_id: str
    sequence: int | None
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
    last_error: str | None = None


_LEGACY_SCOPE_TO_CONTROLLER_ACTIONS = {
    "system:read": (ControllerAction.SYSTEM_READ,),
    "devices:read": (ControllerAction.DEVICES_READ,),
    "events:read": (ControllerAction.EVENTS_READ,),
    "jobs:submit": (ControllerAction.JOBS_CREATE, ControllerAction.JOBS_CANCEL),
    "commands:execute": (ControllerAction.COMMANDS_EXECUTE,),
}


def resolve_mutual_tls_policy(
    configured_mode: MutualTlsMode,
    *,
    certificate_mode: UpstreamCertificateMode,
    client_certificate_present: bool,
    certificate_enrollment: CertificateEnrollmentSpec | None = None,
) -> MutualTlsPolicy:
    if configured_mode is MutualTlsMode.DISABLED:
        return MutualTlsPolicy(
            configured_mode=configured_mode,
            effective_mode=MutualTlsMode.DISABLED,
        )
    if configured_mode is MutualTlsMode.REQUIRED:
        return MutualTlsPolicy(
            configured_mode=configured_mode,
            effective_mode=MutualTlsMode.REQUIRED,
        )
    if certificate_mode is UpstreamCertificateMode.NONE:
        return MutualTlsPolicy(
            configured_mode=configured_mode,
            effective_mode=MutualTlsMode.OPTIONAL,
        )
    if not client_certificate_present:
        return MutualTlsPolicy(
            configured_mode=configured_mode,
            effective_mode=MutualTlsMode.OPTIONAL,
        )
    if (
        certificate_enrollment is not None
        and not certificate_enrollment.requires_mutual_tls_after_issuance
    ):
        return MutualTlsPolicy(
            configured_mode=configured_mode,
            effective_mode=MutualTlsMode.OPTIONAL,
        )
    return MutualTlsPolicy(
        configured_mode=configured_mode,
        effective_mode=MutualTlsMode.REQUIRED,
    )


def parse_controller_actions(values: object) -> tuple[ControllerAction, ...]:
    seen: set[ControllerAction] = set()
    ordered: list[ControllerAction] = []
    if not isinstance(values, (list, tuple)):
        return ()
    for value in values:
        if value is None:
            continue
        try:
            action = ControllerAction(str(value))
        except ValueError:
            for mapped in _LEGACY_SCOPE_TO_CONTROLLER_ACTIONS.get(str(value), ()):
                if mapped not in seen:
                    seen.add(mapped)
                    ordered.append(mapped)
            continue
        if action not in seen:
            seen.add(action)
            ordered.append(action)
    return tuple(ordered)
