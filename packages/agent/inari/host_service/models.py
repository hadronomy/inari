from __future__ import annotations

import platform
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

ServiceScope = Literal["system", "user"]
DEFAULT_SERVICE_SCOPE: ServiceScope = "system"


class ServiceState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    display_name: str
    description: str
    windows_name: str
    systemd_unit_name: str
    launchd_label: str


DEFAULT_SERVICE_IDENTITY = ServiceIdentity(
    display_name="Inari",
    description="Secure local gateway service for physical devices and Inari Device Center.",
    windows_name="InariAgent",
    systemd_unit_name="inari.service",
    launchd_label="io.inari.service",
)


def default_service_name(*, platform_system: str | None = None) -> str:
    current_platform = platform_system or platform.system()
    if current_platform == "Windows":
        return DEFAULT_SERVICE_IDENTITY.windows_name
    if current_platform == "Linux":
        return DEFAULT_SERVICE_IDENTITY.systemd_unit_name
    if current_platform == "Darwin":
        return DEFAULT_SERVICE_IDENTITY.launchd_label
    return DEFAULT_SERVICE_IDENTITY.display_name


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    state: ServiceState
    detail: str


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    format_name: str
    content: str
    path: Path | None = None
