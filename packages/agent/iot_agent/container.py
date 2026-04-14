from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import platform

from .config import AgentSettings, get_settings
from .db import DatabaseMigrator
from .drivers import DriverRegistry
from .drivers.printers import CupsPrinterDriver, RawSocketPrinterDriver, WindowsPrinterDriver, WindowsSpooler
from .gateway.auth_providers import build_upstream_auth_provider
from .gateway.connector import GatewayConnector
from .gateway.enrollment import GatewayEnrollmentService
from .gateway.repositories import GatewayRepository
from .gateway.runtime_bridge import GatewayCommandDispatcher, GatewayRuntimeEventForwarder
from .gateway.service import GatewayService, GatewaySnapshotBuilder
from .gateway.supervisor import GatewaySupervisor
from .printers import PrinterTransport
from .printer_service import PrinterService
from .receipt_renderers import EscPosImageReceiptRenderer, EscPosRenderer
from .runtime.discovery import DiscoveryCoordinator
from .runtime.events import EventHub
from .runtime.execution import DeviceWorkerPool, JobScheduler, LeaseRecoveryCoordinator, PrinterOperationExecutor, RuntimeJobExecutor
from .runtime.repositories import DeviceRepository, JobRepository
from .runtime.services import DeviceCatalog, JobService
from .runtime.store import RuntimeStore
from .runtime.supervisor import RuntimeSupervisor
from .security.auth import AuthorizationService
from .security.certificate_provisioners import build_certificate_provisioner
from .security.certificates import CertificateLifecycleService
from .security.identity import AgentIdentityService
from .security.policies import SecurityPolicyService
from .security.secrets import FileSecretStore, KeyringSecretStore, ResilientSecretStore
from .security.tls import TlsContextFactory
from .security.tokens import TokenService
from .supervision import ApplicationSupervisor


@dataclass(slots=True, frozen=True)
class AgentContainer:
    settings: AgentSettings
    database_migrator: DatabaseMigrator
    driver_registry: DriverRegistry
    printer_service: PrinterService
    event_hub: EventHub
    device_catalog: DeviceCatalog
    job_service: JobService
    runtime_supervisor: RuntimeSupervisor
    identity_service: AgentIdentityService | None = None
    authorization_service: AuthorizationService | None = None
    security_policy_service: SecurityPolicyService | None = None
    tls_context_factory: TlsContextFactory | None = None
    gateway_service: GatewayService | None = None
    gateway_supervisor: GatewaySupervisor | None = None
    application_supervisor: ApplicationSupervisor | None = None


def build_container(settings: AgentSettings) -> AgentContainer:
    database_migrator = DatabaseMigrator(settings.runtime_database_path)
    driver_registry = DriverRegistry(drivers=_build_printer_drivers(settings))
    printer_service = PrinterService(
        settings=settings,
        driver_registry=driver_registry,
        structured_receipt_renderer=EscPosRenderer(),
        image_receipt_renderer=EscPosImageReceiptRenderer(),
    )
    store = RuntimeStore(settings.runtime_database_path)
    event_hub = EventHub()
    device_repository = DeviceRepository(store)
    job_repository = JobRepository(store)
    gateway_repository = GatewayRepository(store)
    discovery = DiscoveryCoordinator(
        driver_registry=driver_registry,
        device_repository=device_repository,
        event_hub=event_hub,
    )
    device_catalog = DeviceCatalog(
        device_repository=device_repository,
        discovery=discovery,
        printer_service=printer_service,
    )
    job_service = JobService(
        settings=settings,
        job_repository=job_repository,
        device_catalog=device_catalog,
        event_hub=event_hub,
    )
    executor = RuntimeJobExecutor(PrinterOperationExecutor(printer_service))
    worker_pool = DeviceWorkerPool(
        settings=settings,
        job_repository=job_repository,
        job_service=job_service,
        executor=executor,
    )
    runtime_supervisor = RuntimeSupervisor(
        settings=settings,
        store=store,
        device_catalog=device_catalog,
        job_service=job_service,
        job_scheduler=JobScheduler(
            settings=settings,
            job_repository=job_repository,
            job_service=job_service,
            worker_pool=worker_pool,
        ),
        lease_recovery=LeaseRecoveryCoordinator(
            settings=settings,
            job_repository=job_repository,
            job_service=job_service,
        ),
        worker_pool=worker_pool,
    )
    security_state_dir = settings.security_state_dir
    identity_path = security_state_dir / "agent-identity.pem"
    upstream_certificate_path = security_state_dir / "upstream-client-cert.pem"
    upstream_ca_path = security_state_dir / "upstream-ca.pem"
    identity_service = AgentIdentityService(
        identity_path=identity_path,
        certificate_path=upstream_certificate_path,
    )
    certificate_service = CertificateLifecycleService(
        certificate_path=upstream_certificate_path,
        private_key_path=identity_path,
        ca_path=upstream_ca_path,
    )
    secret_store = ResilientSecretStore(
        primary=KeyringSecretStore(service_name=settings.secret_store_service_name),
        fallback=FileSecretStore(security_state_dir / "secrets.json"),
    )
    security_policy_service = SecurityPolicyService(settings)
    tls_context_factory = TlsContextFactory(
        settings,
        certificate_service=certificate_service,
    )
    upstream_auth_provider = build_upstream_auth_provider(settings)
    certificate_provisioner = build_certificate_provisioner(
        settings,
        identity_service=identity_service,
        certificate_service=certificate_service,
    )
    token_service = TokenService(
        secret_store=secret_store,
        identity_service=identity_service,
        token_ttl_seconds=settings.local_token_ttl_seconds,
        token_audience=settings.token_audience,
        token_issuer=settings.token_issuer,
    )
    authorization_service = AuthorizationService(
        token_service=token_service,
        policy_service=security_policy_service,
    )
    snapshot_builder = GatewaySnapshotBuilder(
        settings=settings,
        identity_service=identity_service,
        device_catalog=device_catalog,
        job_service=job_service,
        gateway_repository=gateway_repository,
        security_policy_service=security_policy_service,
        certificate_service=certificate_service,
    )
    gateway_connector = GatewayConnector(
        settings=settings,
        enrollment_service=GatewayEnrollmentService(
            settings=settings,
            identity_service=identity_service,
            secret_store=secret_store,
            tls_context_factory=tls_context_factory,
            certificate_service=certificate_service,
            auth_provider=upstream_auth_provider,
            certificate_provisioner=certificate_provisioner,
            metadata_path=security_state_dir / "upstream-enrollment.json",
            snapshot_provider=lambda: snapshot_builder.build_snapshot().model_dump(mode="json"),
        ),
        tls_context_factory=tls_context_factory,
        snapshot_provider=lambda: snapshot_builder.build_snapshot().model_dump(mode="json"),
        gateway_repository=gateway_repository,
        command_dispatcher=GatewayCommandDispatcher(
            job_service=job_service,
            gateway_repository=gateway_repository,
        ),
    )
    gateway_service = GatewayService(
        settings=settings,
        identity_service=identity_service,
        connector=gateway_connector,
        snapshot_builder=snapshot_builder,
    )
    gateway_supervisor = GatewaySupervisor(
        settings=settings,
        connector=gateway_connector,
        runtime_event_forwarder=GatewayRuntimeEventForwarder(
            event_hub=event_hub,
            gateway_repository=gateway_repository,
        ),
    )
    application_supervisor = ApplicationSupervisor(
        runtime_supervisor=runtime_supervisor,
        gateway_supervisor=gateway_supervisor,
    )
    return AgentContainer(
        settings=settings,
        database_migrator=database_migrator,
        driver_registry=driver_registry,
        printer_service=printer_service,
        event_hub=event_hub,
        device_catalog=device_catalog,
        job_service=job_service,
        runtime_supervisor=runtime_supervisor,
        identity_service=identity_service,
        authorization_service=authorization_service,
        security_policy_service=security_policy_service,
        tls_context_factory=tls_context_factory,
        gateway_service=gateway_service,
        gateway_supervisor=gateway_supervisor,
        application_supervisor=application_supervisor,
    )


def _build_printer_drivers(settings: AgentSettings, *, platform_system: str | None = None) -> tuple:
    current_platform = platform_system or platform.system()
    drivers = []
    if current_platform == "Windows":
        drivers.append(
            WindowsPrinterDriver(
                spooler=WindowsSpooler(),
                default_transport=PrinterTransport(settings.default_printer_mode),
            )
        )
    elif current_platform in {"Linux", "Darwin"}:
        drivers.append(
            CupsPrinterDriver(
                default_transport=PrinterTransport(settings.default_printer_mode),
            )
        )
    if settings.network_printers:
        drivers.append(
            RawSocketPrinterDriver(
                configured_printers=tuple(settings.network_printers),
            )
        )
    return tuple(drivers)


@lru_cache(maxsize=1)
def get_default_container() -> AgentContainer:
    return build_container(get_settings())
