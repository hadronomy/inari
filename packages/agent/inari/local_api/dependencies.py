from __future__ import annotations

from fastapi import Depends
from starlette.requests import HTTPConnection

from ..config import AgentSettings
from ..client_trust import ClientTrustService
from ..application.container import AgentContainer, get_default_container
from .device_work import DeviceWorkSubmission
from .print_job_queries import PrintJobQueries
from ..gateway.service import GatewayService
from ..gateway.onboarding import ManagedOnboardingService
from ..runtime.events import EventHub
from ..runtime.devices.service import DeviceCatalog
from ..runtime.jobs.service import JobService
from ..security.auth import AuthorizationService
from ..security.identity import AgentIdentityService
from ..security.local_trust import StandaloneTrustService
from ..security.policies import SecurityPolicyService


def get_container(connection: HTTPConnection) -> AgentContainer:
    container = getattr(connection.app.state, "container", None)
    if container is None:
        container = get_default_container()
        connection.app.state.container = container
    return container


def get_settings(container: AgentContainer = Depends(get_container)) -> AgentSettings:
    return container.settings


def get_device_catalog(
    container: AgentContainer = Depends(get_container),
) -> DeviceCatalog:
    return container.device_catalog


def get_job_service(container: AgentContainer = Depends(get_container)) -> JobService:
    return container.job_service


def get_device_work_submission(
    container: AgentContainer = Depends(get_container),
) -> DeviceWorkSubmission:
    return container.device_work_submission


def get_print_job_queries(
    container: AgentContainer = Depends(get_container),
) -> PrintJobQueries:
    return container.print_job_queries


def get_client_trust_service(
    container: AgentContainer = Depends(get_container),
) -> ClientTrustService:
    if container.client_trust_service is None:
        raise RuntimeError("ClientTrustService is not configured.")
    return container.client_trust_service


def get_event_hub(container: AgentContainer = Depends(get_container)) -> EventHub:
    return container.event_hub


def get_authorization_service(
    container: AgentContainer = Depends(get_container),
) -> AuthorizationService:
    if container.authorization_service is None:
        raise RuntimeError("AuthorizationService is not configured.")
    return container.authorization_service


def get_standalone_trust_service(
    container: AgentContainer = Depends(get_container),
) -> StandaloneTrustService:
    if container.standalone_trust_service is None:
        raise RuntimeError("StandaloneTrustService is not configured.")
    return container.standalone_trust_service


def get_security_policy_service(
    container: AgentContainer = Depends(get_container),
) -> SecurityPolicyService:
    if container.security_policy_service is None:
        raise RuntimeError("SecurityPolicyService is not configured.")
    return container.security_policy_service


def get_identity_service(
    container: AgentContainer = Depends(get_container),
) -> AgentIdentityService:
    if container.identity_service is None:
        raise RuntimeError("AgentIdentityService is not configured.")
    return container.identity_service


def get_gateway_service(
    container: AgentContainer = Depends(get_container),
) -> GatewayService:
    if container.gateway_service is None:
        raise RuntimeError("GatewayService is not configured.")
    return container.gateway_service


def get_onboarding_service(
    container: AgentContainer = Depends(get_container),
) -> ManagedOnboardingService:
    if container.onboarding_service is None:
        raise RuntimeError("ManagedOnboardingService is not configured.")
    return container.onboarding_service
