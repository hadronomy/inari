from __future__ import annotations

from dataclasses import dataclass

from dishka import Provider, Scope, provide

from ..config import AgentSettings
from ..gateway.auth_providers import UpstreamAuthProvider
from ..gateway.connector import GatewayConnector
from ..gateway.data_plane import ZenohGatewayTransport
from ..gateway.enrollment import GatewayEnrollmentService
from ..gateway.repositories import GatewayRepository
from ..gateway.runtime_bridge import (
    GatewayCommandDispatcher,
    GatewayRuntimeEventForwarder,
)
from ..gateway.service import GatewayService, GatewaySnapshotBuilder
from ..gateway.supervisor import GatewaySupervisor
from ..runtime.services import DeviceCatalog, JobService
from ..security.certificate_lifecycle import ManagedCertificateLifecycleManager
from ..security.certificate_provisioners import ClientCertificateProvisioner
from ..security.certificates import CertificateLifecycleService
from ..security.identity import AgentIdentityService
from ..security.policies import SecurityPolicyService
from ..security.secrets import ResilientSecretStore
from ..security.tls import TlsContextFactory


@dataclass(slots=True, frozen=True)
class GatewayStack:
    snapshot_builder: GatewaySnapshotBuilder
    enrollment_service: GatewayEnrollmentService
    certificate_lifecycle_manager: ManagedCertificateLifecycleManager
    connector: GatewayConnector
    gateway_service: GatewayService
    gateway_supervisor: GatewaySupervisor


class GatewayProvider(Provider):
    scope = Scope.APP

    gateway_command_dispatcher = provide(GatewayCommandDispatcher)
    gateway_runtime_event_forwarder = provide(GatewayRuntimeEventForwarder)

    @provide
    def zenoh_gateway_transport(
        self,
        settings: AgentSettings,
        certificate_lifecycle_service: CertificateLifecycleService,
    ) -> ZenohGatewayTransport:
        return ZenohGatewayTransport(
            settings=settings,
            certificate_service=certificate_lifecycle_service,
        )

    @provide
    def gateway_stack(
        self,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        device_catalog: DeviceCatalog,
        job_service: JobService,
        gateway_repository: GatewayRepository,
        security_policy_service: SecurityPolicyService,
        certificate_lifecycle_service: CertificateLifecycleService,
        secret_store: ResilientSecretStore,
        tls_context_factory: TlsContextFactory,
        upstream_auth_provider: UpstreamAuthProvider,
        certificate_provisioner: ClientCertificateProvisioner,
        gateway_command_dispatcher: GatewayCommandDispatcher,
        gateway_runtime_event_forwarder: GatewayRuntimeEventForwarder,
        zenoh_gateway_transport: ZenohGatewayTransport,
    ) -> GatewayStack:
        snapshot_builder = GatewaySnapshotBuilder(
            settings=settings,
            identity_service=identity_service,
            device_catalog=device_catalog,
            job_service=job_service,
            gateway_repository=gateway_repository,
            security_policy_service=security_policy_service,
            certificate_service=certificate_lifecycle_service,
            certificate_lifecycle_manager=None,
        )
        enrollment_service = GatewayEnrollmentService(
            settings=settings,
            identity_service=identity_service,
            secret_store=secret_store,
            tls_context_factory=tls_context_factory,
            certificate_service=certificate_lifecycle_service,
            auth_provider=upstream_auth_provider,
            metadata_path=settings.resolved_security_state_dir
            / "upstream-enrollment.json",
            snapshot_provider=snapshot_builder.build_snapshot,
        )
        certificate_lifecycle_manager = ManagedCertificateLifecycleManager(
            settings=settings,
            enrollment_service=enrollment_service,
            certificate_service=certificate_lifecycle_service,
            certificate_provisioner=certificate_provisioner,
        )
        snapshot_builder.certificate_lifecycle_manager = certificate_lifecycle_manager
        connector = GatewayConnector(
            settings=settings,
            enrollment_service=enrollment_service,
            certificate_lifecycle_manager=certificate_lifecycle_manager,
            snapshot_provider=snapshot_builder.build_snapshot,
            gateway_repository=gateway_repository,
            command_dispatcher=gateway_command_dispatcher,
            data_plane_transport=zenoh_gateway_transport,
        )
        gateway_service = GatewayService(
            settings=settings,
            identity_service=identity_service,
            connector=connector,
            snapshot_builder=snapshot_builder,
            certificate_lifecycle_manager=certificate_lifecycle_manager,
        )
        gateway_supervisor = GatewaySupervisor(
            settings=settings,
            connector=connector,
            certificate_lifecycle_manager=certificate_lifecycle_manager,
            runtime_event_forwarder=gateway_runtime_event_forwarder,
        )
        return GatewayStack(
            snapshot_builder=snapshot_builder,
            enrollment_service=enrollment_service,
            certificate_lifecycle_manager=certificate_lifecycle_manager,
            connector=connector,
            gateway_service=gateway_service,
            gateway_supervisor=gateway_supervisor,
        )

    @provide
    def snapshot_builder(self, gateway_stack: GatewayStack) -> GatewaySnapshotBuilder:
        return gateway_stack.snapshot_builder

    @provide
    def enrollment_service(
        self, gateway_stack: GatewayStack
    ) -> GatewayEnrollmentService:
        return gateway_stack.enrollment_service

    @provide
    def certificate_lifecycle_manager(
        self,
        gateway_stack: GatewayStack,
    ) -> ManagedCertificateLifecycleManager:
        return gateway_stack.certificate_lifecycle_manager

    @provide
    def gateway_connector(self, gateway_stack: GatewayStack) -> GatewayConnector:
        return gateway_stack.connector

    @provide
    def gateway_service(self, gateway_stack: GatewayStack) -> GatewayService:
        return gateway_stack.gateway_service

    @provide
    def gateway_supervisor(self, gateway_stack: GatewayStack) -> GatewaySupervisor:
        return gateway_stack.gateway_supervisor
