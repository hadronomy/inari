from __future__ import annotations

from fastapi import Depends, Request

from .config import AgentSettings
from .container import AgentContainer, get_default_container
from .printer_service import PrinterService
from .runtime.manager import AgentRuntime


def get_container(request: Request) -> AgentContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        container = get_default_container()
        request.app.state.container = container
    return container


def get_settings(container: AgentContainer = Depends(get_container)) -> AgentSettings:
    return container.settings


def get_printer_service(container: AgentContainer = Depends(get_container)) -> PrinterService:
    return container.printer_service


def get_runtime(container: AgentContainer = Depends(get_container)) -> AgentRuntime:
    return container.runtime
