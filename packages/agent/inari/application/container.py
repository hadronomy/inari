from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from dishka import make_container

from ..config import AgentSettings, get_settings
from ..client_trust import ClientTrustService
from ..db import DatabaseMigrator
from ..di import (
    AppProvider,
    DriverProvider,
    GatewayProvider,
    RuntimeProvider,
    SecurityProvider,
)
from ..drivers import DriverRegistry
from ..documents import DocumentAdmission
from ..gateway.service import GatewayService
from ..gateway.onboarding import ManagedOnboardingService
from ..gateway.supervisor import GatewaySupervisor
from ..local_api.device_work import DeviceWorkSubmission
from ..local_api.header_authorization import ClientTrustAuthorizer
from ..local_api.print_job_queries import PrintJobQueries
from ..drawer_intents import DrawerIntentService
from ..printing.service import PrinterService
from ..physical_execution import PhysicalExecution
from ..runtime.events import EventHub
from ..runtime.devices.service import DeviceCatalog
from ..runtime.jobs.service import JobService
from ..runtime.supervisor import RuntimeSupervisor
from ..security.auth import AuthorizationService
from ..security.certificates.lifecycle import ManagedCertificateLifecycleManager
from ..security.identity import AgentIdentityService
from ..security.local_trust import StandaloneTrustService
from ..security.policies import SecurityPolicyService
from ..security.tls import TlsContextFactory
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
    document_admission: DocumentAdmission
    device_work_submission: DeviceWorkSubmission
    print_job_queries: PrintJobQueries
    drawer_intent_service: DrawerIntentService
    physical_execution: PhysicalExecution
    device_work_authorizer: ClientTrustAuthorizer | None = None
    client_trust_service: ClientTrustService | None = None
    identity_service: AgentIdentityService | None = None
    authorization_service: AuthorizationService | None = None
    standalone_trust_service: StandaloneTrustService | None = None
    security_policy_service: SecurityPolicyService | None = None
    tls_context_factory: TlsContextFactory | None = None
    certificate_lifecycle_manager: ManagedCertificateLifecycleManager | None = None
    gateway_service: GatewayService | None = None
    onboarding_service: ManagedOnboardingService | None = None
    gateway_supervisor: GatewaySupervisor | None = None
    application_supervisor: ApplicationSupervisor | None = None


def build_container(settings: AgentSettings) -> AgentContainer:
    dependency_container = make_container(
        AppProvider(),
        DriverProvider(),
        RuntimeProvider(),
        SecurityProvider(),
        GatewayProvider(),
        context={AgentSettings: settings},
    )
    return AgentContainer(
        settings=settings,
        database_migrator=dependency_container.get(DatabaseMigrator),
        driver_registry=dependency_container.get(DriverRegistry),
        printer_service=dependency_container.get(PrinterService),
        event_hub=dependency_container.get(EventHub),
        device_catalog=dependency_container.get(DeviceCatalog),
        job_service=dependency_container.get(JobService),
        runtime_supervisor=dependency_container.get(RuntimeSupervisor),
        document_admission=dependency_container.get(DocumentAdmission),
        device_work_submission=dependency_container.get(DeviceWorkSubmission),
        print_job_queries=dependency_container.get(PrintJobQueries),
        drawer_intent_service=dependency_container.get(DrawerIntentService),
        physical_execution=dependency_container.get(PhysicalExecution),
        device_work_authorizer=dependency_container.get(ClientTrustAuthorizer),
        client_trust_service=dependency_container.get(ClientTrustService),
        identity_service=dependency_container.get(AgentIdentityService),
        authorization_service=dependency_container.get(AuthorizationService),
        standalone_trust_service=dependency_container.get(StandaloneTrustService),
        security_policy_service=dependency_container.get(SecurityPolicyService),
        tls_context_factory=dependency_container.get(TlsContextFactory),
        certificate_lifecycle_manager=dependency_container.get(
            ManagedCertificateLifecycleManager
        ),
        gateway_service=dependency_container.get(GatewayService),
        onboarding_service=dependency_container.get(ManagedOnboardingService),
        gateway_supervisor=dependency_container.get(GatewaySupervisor),
        application_supervisor=dependency_container.get(ApplicationSupervisor),
    )


@lru_cache(maxsize=1)
def get_default_container() -> AgentContainer:
    return build_container(get_settings())
