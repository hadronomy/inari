from __future__ import annotations

from fastapi import Depends
from starlette.requests import HTTPConnection

from ..config import AgentSettings
from ..application.container import AgentContainer, get_default_container
from ..gateway.service import GatewayService
from ..runtime.events import EventHub
from ..runtime.devices.service import DeviceCatalog
from ..runtime.jobs.service import JobService
from ..security.auth import AuthorizationService
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


def get_event_hub(container: AgentContainer = Depends(get_container)) -> EventHub:
    return container.event_hub


def get_authorization_service(
    container: AgentContainer = Depends(get_container),
) -> AuthorizationService:
    if container.authorization_service is None:
        raise RuntimeError("AuthorizationService is not configured.")
    return container.authorization_service


def get_security_policy_service(
    container: AgentContainer = Depends(get_container),
) -> SecurityPolicyService:
    if container.security_policy_service is None:
        raise RuntimeError("SecurityPolicyService is not configured.")
    return container.security_policy_service


def get_gateway_service(
    container: AgentContainer = Depends(get_container),
) -> GatewayService:
    if container.gateway_service is None:
        raise RuntimeError("GatewayService is not configured.")
    return container.gateway_service
