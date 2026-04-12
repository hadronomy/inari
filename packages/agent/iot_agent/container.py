from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .config import AgentSettings, get_settings
from .drivers import DriverRegistry
from .drivers.printers import WindowsPrinterDriver, WindowsSpooler
from .gateway import GatewayConnector, GatewayEnrollmentService, GatewayService, GatewaySupervisor
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
from .security.identity import AgentIdentityService
from .security.policies import SecurityPolicyService
from .security.secrets import FileSecretStore, KeyringSecretStore, ResilientSecretStore
from .security.tls import TlsContextFactory
from .security.tokens import TokenService
from .supervision import ApplicationSupervisor


@dataclass(slots=True, frozen=True)
class AgentContainer:
    settings: AgentSettings
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
    driver_registry = DriverRegistry(
        drivers=(
            WindowsPrinterDriver(
                spooler=WindowsSpooler(),
                default_transport=PrinterTransport(settings.default_printer_mode),
            ),
        )
    )
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
    identity_service = AgentIdentityService(
        identity_path=security_state_dir / "agent-identity.pem",
        certificate_path=settings.tls_cert_path,
    )
    secret_store = ResilientSecretStore(
        primary=KeyringSecretStore(service_name=settings.secret_store_service_name),
        fallback=FileSecretStore(security_state_dir / "secrets.json"),
    )
    security_policy_service = SecurityPolicyService(settings)
    tls_context_factory = TlsContextFactory(settings)
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
    gateway_connector = GatewayConnector(
        settings=settings,
        enrollment_service=GatewayEnrollmentService(
            settings=settings,
            identity_service=identity_service,
            secret_store=secret_store,
            tls_context_factory=tls_context_factory,
            metadata_path=security_state_dir / "upstream-enrollment.json",
        ),
        tls_context_factory=tls_context_factory,
        snapshot_provider=lambda: {
            "service": {"name": "IoT Agent"},
            "devices": {"count": len(device_catalog.list_devices())},
            "queue": dict(job_service.queue_counts()),
        },
    )
    gateway_service = GatewayService(
        settings=settings,
        identity_service=identity_service,
        connector=gateway_connector,
    )
    gateway_supervisor = GatewaySupervisor(
        settings=settings,
        connector=gateway_connector,
    )
    application_supervisor = ApplicationSupervisor(
        runtime_supervisor=runtime_supervisor,
        gateway_supervisor=gateway_supervisor,
    )
    return AgentContainer(
        settings=settings,
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


@lru_cache(maxsize=1)
def get_default_container() -> AgentContainer:
    return build_container(get_settings())
