from __future__ import annotations

from ..config import AgentSettings
from ..device_commands import DeviceCommandKind
from ..print_jobs import PrintContentKind
from ..runtime.services import DeviceCatalog, JobService
from ..security.certificates import CertificateLifecycleService
from ..security.identity import AgentIdentityService
from ..security.models import UPSTREAM_AGENT_SCOPES
from ..security.policies import SecurityPolicyService
from ..version import API_VERSION, SERVICE_NAME
from .caddy import CaddyControllerProfile
from .connector import GatewayConnector
from .protocol import (
    GatewayCapabilityDescriptor,
    GatewayDeviceSummary,
    GatewayProtocolDescriptor,
    GatewayQueueSummary,
    GatewayRuntimeSummary,
    GatewaySecurityDescriptor,
    GatewaySnapshotPayload,
)
from .repositories import GatewayRepository


class GatewaySnapshotBuilder:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        device_catalog: DeviceCatalog,
        job_service: JobService,
        gateway_repository: GatewayRepository,
        security_policy_service: SecurityPolicyService,
        certificate_service: CertificateLifecycleService,
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.device_catalog = device_catalog
        self.job_service = job_service
        self.gateway_repository = gateway_repository
        self.security_policy_service = security_policy_service
        self.certificate_service = certificate_service

    def build_snapshot(self) -> GatewaySnapshotPayload:
        from ..models import DeviceDirectorySummaryResponse, QueueSummaryResponse
        from ..runtime.models import utc_now

        identity = self.identity_service.get_or_create_identity()
        devices = list(self.device_catalog.list_devices())
        device_summary = DeviceDirectorySummaryResponse.from_devices(devices)
        certificate = self.certificate_service.current_certificate()

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
                auth_mode=self.settings.upstream_auth_mode,
                certificate_mode=self.settings.upstream_certificate_mode,
                mutual_tls_mode=self.settings.upstream_mutual_tls_mode,
                mutual_tls_enabled=certificate is not None,
                certificate_expires_at=certificate.not_valid_after if certificate is not None else None,
            ),
            runtime=GatewayRuntimeSummary(
                queue=GatewayQueueSummary.model_validate(
                    QueueSummaryResponse.from_counts(dict(self.job_service.queue_counts())).model_dump(mode="json")
                ),
                devices=GatewayDeviceSummary(
                    count=device_summary.count,
                    online_count=device_summary.online_count,
                    offline_count=device_summary.offline_count,
                    kind_counts=dict(device_summary.kind_counts),
                    default_device_id=device_summary.default_device.id if device_summary.default_device is not None else None,
                    default_device_name=(
                        device_summary.default_device.name if device_summary.default_device is not None else None
                    ),
                ),
            ),
            capabilities=GatewayCapabilityDescriptor(
                supported_content_kinds=tuple(kind.value for kind in PrintContentKind),
                supported_device_commands=tuple(kind.value for kind in DeviceCommandKind),
                granted_scopes=UPSTREAM_AGENT_SCOPES,
                features=(
                    "status_sync",
                    "control_stream",
                    "command_ack",
                    "event_replay",
                    "runtime_event_forwarding",
                    "token_refresh",
                    "certificate_rotation",
                    "protocol_negotiation",
                    *(("enrollment_token_bootstrap",) if self.settings.upstream_enrollment_token else ()),
                    *(("zitadel_private_key_jwt",) if self.settings.upstream_auth_mode.value == "zitadel_service_account" else ()),
                    *(("step_ca_client_certificates", "step_ca_ott_bootstrap") if self.settings.upstream_certificate_mode.value == "step_ca" else ()),
                    *(("caddy_edge",) if CaddyControllerProfile.from_settings(self.settings).enabled else ()),
                ),
                client_certificate_present=certificate is not None,
            ),
            observability={
                "gateway": self.gateway_repository.summary(),
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
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.connector = connector
        self.snapshot_builder = snapshot_builder

    def get_identity(self):
        return self.identity_service.get_or_create_identity()

    def get_upstream_status(self):
        return self.connector.current_status()

    def build_snapshot(self) -> GatewaySnapshotPayload:
        return self.snapshot_builder.build_snapshot()
