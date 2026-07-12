from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Mapping, Protocol

from ..printing.commands import DeviceCommandKind
from ..printing.jobs import PrintContentKind
from ..core.version import API_VERSION, SERVICE_NAME
from .edge.caddy import CaddyControllerProfile
from .models import SUPPORTED_CONTROLLER_ACTIONS, resolve_mutual_tls_policy
from .protocol import (
    GatewayCapabilityDescriptor,
    GatewayDeviceInventory,
    GatewayDeviceInventoryItem,
    GatewayDeviceSummary,
    JSON_OBJECT_ADAPTER,
    GatewayProtocolDescriptor,
    GatewayQueueSummary,
    GatewayRuntimeSummary,
    GatewaySecurityDescriptor,
    GatewaySnapshotPayload,
)

if TYPE_CHECKING:
    from ..config import AgentSettings
    from ..runtime.models import DeviceRecord
    from ..security.certificates.lifecycle import ManagedCertificateLifecycleManager
    from ..security.certificates.store import ManagedCertificate
    from ..security.identity import AgentIdentityService
    from ..security.models import AgentIdentity, GatewaySecurityPolicy
    from .connector import GatewayConnector
    from .models import ManagedCertificateStatus


class IdentitySource(Protocol):
    def get_or_create_identity(self) -> AgentIdentity: ...


class DeviceInventory(Protocol):
    def list_devices(self) -> Iterable[DeviceRecord]: ...


class QueueMetrics(Protocol):
    def queue_counts(self) -> Mapping[str, int]: ...


class GatewayMetrics(Protocol):
    def summary(self) -> dict[str, int]: ...


class SecurityPolicyView(Protocol):
    @property
    def policy(self) -> GatewaySecurityPolicy: ...


class CertificateSource(Protocol):
    def current_certificate(self) -> ManagedCertificate | None: ...


class CertificateMonitor(Protocol):
    def current_status(self) -> ManagedCertificateStatus: ...


class GatewaySnapshotBuilder:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_service: IdentitySource,
        device_catalog: DeviceInventory,
        job_service: QueueMetrics,
        gateway_repository: GatewayMetrics,
        security_policy_service: SecurityPolicyView,
        certificate_service: CertificateSource,
        certificate_lifecycle_manager: CertificateMonitor | None,
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.device_catalog = device_catalog
        self.job_service = job_service
        self.gateway_repository = gateway_repository
        self.security_policy_service = security_policy_service
        self.certificate_service = certificate_service
        self.certificate_lifecycle_manager = certificate_lifecycle_manager

    def build_snapshot(self) -> GatewaySnapshotPayload:
        from ..local_api.schemas import (
            DeviceDirectorySummaryResponse,
            QueueSummaryResponse,
        )
        from ..runtime.models import utc_now

        identity = self.identity_service.get_or_create_identity()
        devices = list(self.device_catalog.list_devices())
        device_summary = DeviceDirectorySummaryResponse.from_devices(devices)
        certificate = self.certificate_service.current_certificate()
        certificate_lifecycle = (
            self.certificate_lifecycle_manager.current_status()
            if self.certificate_lifecycle_manager is not None
            else None
        )
        mutual_tls_policy = resolve_mutual_tls_policy(
            self.settings.upstream_mutual_tls_mode,
            certificate_mode=self.settings.upstream_certificate_mode,
            client_certificate_present=certificate is not None,
        )

        return GatewaySnapshotPayload(
            generated_at=utc_now(),
            protocol=GatewayProtocolDescriptor(),
            service={
                "name": SERVICE_NAME,
                "version": API_VERSION,
                "agent_id": identity.agent_id,
                "key_id": identity.key_id,
            },
            security=GatewaySecurityDescriptor(
                mode=self.settings.gateway_mode,
                exposure=self.settings.gateway_exposure,
                tls_required=self.security_policy_service.policy.require_tls,
                edge_provider=self.settings.upstream_edge_provider,
                certificate_mode=self.settings.upstream_certificate_mode,
                mutual_tls_mode=mutual_tls_policy.effective_mode,
                mutual_tls_enabled=mutual_tls_policy.enabled
                and certificate is not None,
                certificate_expires_at=certificate.not_valid_after
                if certificate is not None
                else None,
            ),
            runtime=GatewayRuntimeSummary(
                queue=GatewayQueueSummary.model_validate(
                    QueueSummaryResponse.from_counts(
                        dict(self.job_service.queue_counts())
                    ).model_dump(mode="json")
                ),
                devices=GatewayDeviceSummary(
                    count=device_summary.count,
                    online_count=device_summary.online_count,
                    offline_count=device_summary.offline_count,
                    kind_counts=dict(device_summary.kind_counts),
                    default_device_id=device_summary.default_device.id
                    if device_summary.default_device is not None
                    else None,
                    default_device_name=(
                        device_summary.default_device.name
                        if device_summary.default_device is not None
                        else None
                    ),
                ),
                inventory=GatewayDeviceInventory(
                    devices=tuple(
                        GatewayDeviceInventoryItem(
                            device_id=device.id,
                            kind=device.kind.value,
                            device_class=device.device_class.value,
                            display_name=self.settings.device_labels.get(
                                device.id, device.name
                            ),
                            system_name=device.name,
                            driver_key=device.driver_key,
                            connection_state=device.connection_state.value,
                            capabilities=tuple(
                                key
                                for key, enabled in device.capabilities.items()
                                if enabled
                            ),
                            metadata=JSON_OBJECT_ADAPTER.validate_python(
                                _redact_device_metadata(dict(device.metadata))
                            ),
                        )
                        for device in devices
                    )
                ),
            ),
            capabilities=GatewayCapabilityDescriptor(
                supported_content_kinds=tuple(kind.value for kind in PrintContentKind),
                supported_device_commands=tuple(
                    kind.value for kind in DeviceCommandKind
                ),
                supported_controller_actions=SUPPORTED_CONTROLLER_ACTIONS,
                features=(
                    "zenoh_data_plane",
                    "status_publication",
                    "command_history_recovery",
                    "liveliness_presence",
                    "runtime_event_forwarding",
                    "certificate_rotation",
                    "protocol_negotiation",
                    "https_enrollment",
                    *(
                        ("enrollment_token_bootstrap",)
                        if self.settings.upstream_enrollment_token
                        else ()
                    ),
                    *(
                        ("zitadel_private_key_jwt",)
                        if self.settings.upstream_auth_mode.value
                        == "zitadel_service_account"
                        else ()
                    ),
                    *(
                        ("controller_enrollment_http_auth",)
                        if self.settings.upstream_auth_mode.value == "controller"
                        else ()
                    ),
                    *(
                        (
                            "managed_client_certificates",
                            "certificate_bootstrap_auth",
                            "certificate_lifecycle_supervision",
                            "step_ca_provider",
                        )
                        if self.settings.upstream_certificate_mode.value == "step_ca"
                        else ()
                    ),
                    *(
                        ("caddy_edge",)
                        if CaddyControllerProfile.from_settings(self.settings).enabled
                        else ()
                    ),
                ),
                client_certificate_present=certificate is not None,
            ),
            observability={
                "gateway": self.gateway_repository.summary(),
                "certificate_lifecycle": _serialize_certificate_lifecycle(
                    certificate_lifecycle
                ),
                "runtime": {
                    "queue_states": dict(self.job_service.queue_counts()),
                },
            },
        )


class GatewayService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        connector: GatewayConnector,
        snapshot_builder: GatewaySnapshotBuilder,
        certificate_lifecycle_manager: ManagedCertificateLifecycleManager | None,
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.connector = connector
        self.snapshot_builder = snapshot_builder
        self.certificate_lifecycle_manager = certificate_lifecycle_manager

    def get_identity(self):
        return self.identity_service.get_or_create_identity()

    def get_upstream_status(self):
        return self.connector.current_status(
            certificate_lifecycle=self.certificate_lifecycle_manager.current_status()
            if self.certificate_lifecycle_manager is not None
            else None
        )

    def build_snapshot(self) -> GatewaySnapshotPayload:
        return self.snapshot_builder.build_snapshot()


def _serialize_certificate_lifecycle(status) -> dict[str, object] | None:
    if status is None:
        return None
    return {
        "state": status.state.value,
        "operation": status.operation.value,
        "failure_reason": status.failure_reason.value,
        "detail": status.detail,
        "current_expires_at": status.current_expires_at.isoformat()
        if status.current_expires_at is not None
        else None,
        "last_checked_at": status.last_checked_at.isoformat()
        if status.last_checked_at is not None
        else None,
        "last_operation_at": status.last_operation_at.isoformat()
        if status.last_operation_at is not None
        else None,
        "last_success_at": status.last_success_at.isoformat()
        if status.last_success_at is not None
        else None,
        "last_failure_at": status.last_failure_at.isoformat()
        if status.last_failure_at is not None
        else None,
        "next_action_at": status.next_action_at.isoformat()
        if status.next_action_at is not None
        else None,
        "retry_delay_seconds": status.retry_delay_seconds,
        "certificate_present": status.certificate_present,
        "bootstrap_pending": status.bootstrap_pending,
        "subject": status.subject,
        "issuer": status.issuer,
        "serial_number": status.serial_number,
        "successful_issue_count": status.successful_issue_count,
        "failed_issue_count": status.failed_issue_count,
        "successful_renewal_count": status.successful_renewal_count,
        "failed_renewal_count": status.failed_renewal_count,
    }


_SENSITIVE_METADATA_PARTS = (
    "credential",
    "password",
    "private",
    "secret",
    "token",
)


def _redact_device_metadata(value: dict[str, object]) -> dict[str, object]:
    return {
        str(key): item
        for key, item in value.items()
        if not any(part in str(key).casefold() for part in _SENSITIVE_METADATA_PARTS)
    }
