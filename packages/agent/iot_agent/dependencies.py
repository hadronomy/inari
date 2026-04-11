from __future__ import annotations

from fastapi import Depends, Request

from .config import AgentSettings
from .container import AgentContainer, get_default_container
from .runtime.events import EventHub
from .runtime.services import DeviceCatalog, JobService


def get_container(request: Request) -> AgentContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        container = get_default_container()
        request.app.state.container = container
    return container


def get_settings(container: AgentContainer = Depends(get_container)) -> AgentSettings:
    return container.settings


def get_device_catalog(container: AgentContainer = Depends(get_container)) -> DeviceCatalog:
    return container.device_catalog


def get_job_service(container: AgentContainer = Depends(get_container)) -> JobService:
    return container.job_service


def get_event_hub(container: AgentContainer = Depends(get_container)) -> EventHub:
    return container.event_hub
