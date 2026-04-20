from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import AgentSettings, load_settings, write_default_config_file
from ..config_paths import resolve_default_path_bundle
from .models import (
    DEFAULT_SERVICE_IDENTITY,
    DEFAULT_SERVICE_SCOPE,
    ServiceDefinition,
    ServiceIdentity,
    ServiceScope,
    ServiceStatus,
)


class ServiceManager(Protocol):
    identity: ServiceIdentity
    scope: ServiceScope

    def install(self) -> str: ...

    def uninstall(self) -> str: ...

    def start(self) -> str: ...

    def stop(self) -> str: ...

    def restart(self) -> str: ...

    def status(self) -> ServiceStatus: ...

    def definition(self) -> ServiceDefinition: ...


@dataclass(frozen=True, slots=True)
class ServiceContext:
    settings: AgentSettings
    identity: ServiceIdentity
    scope: ServiceScope
    config_path: Path
    working_directory: Path
    python_executable: Path


def resolve_service_config_path(
    config_path: Path | str | None = None,
    *,
    platform_system: str | None = None,
) -> Path:
    if config_path is not None:
        return Path(config_path).expanduser().resolve()
    production_bundle = resolve_default_path_bundle(
        profile="production",
        working_directory=Path.cwd(),
        platform_system=platform_system,
    )
    return production_bundle.config_file


def build_service_context(
    settings: AgentSettings,
    *,
    config_path: Path | str | None = None,
    scope: ServiceScope = DEFAULT_SERVICE_SCOPE,
    identity: ServiceIdentity = DEFAULT_SERVICE_IDENTITY,
    platform_system: str | None = None,
) -> ServiceContext:
    resolved_config_path = resolve_service_config_path(
        config_path, platform_system=platform_system
    )
    working_directory = (
        settings.data_dir or resolved_config_path.parent
    ).resolve()
    return ServiceContext(
        settings=settings,
        identity=identity,
        scope=scope,
        config_path=resolved_config_path,
        working_directory=working_directory,
        python_executable=Path(sys.executable).resolve(),
    )


def load_service_settings(
    config_path: Path | str | None = None,
) -> tuple[AgentSettings, Path]:
    resolved_config_path = resolve_service_config_path(config_path)
    if resolved_config_path.exists():
        return load_settings(config_path=resolved_config_path), resolved_config_path
    return AgentSettings.model_validate(
        {"path_profile": "production"}
    ), resolved_config_path


def ensure_service_config_file(config_path: Path) -> bool:
    if config_path.exists():
        return False
    write_default_config_file(
        config_path,
        profile="production",
        overwrite=False,
        schema_path=None,
    )
    return True


def validate_service_config_file(config_path: Path) -> None:
    if config_path.exists():
        return
    raise RuntimeError(
        f'Config file not found at {config_path}. Run `inari config write-default --config "{config_path}"` first.'
    )


def build_service_manager(
    settings: AgentSettings,
    *,
    config_path: Path | str | None = None,
    scope: ServiceScope = DEFAULT_SERVICE_SCOPE,
    platform_system: str | None = None,
) -> ServiceManager:
    current_platform = platform_system or platform.system()
    if current_platform == "Windows" and scope != "system":
        raise RuntimeError("Windows services only support the system scope.")
    context = build_service_context(
        settings,
        config_path=config_path,
        scope=scope,
        platform_system=current_platform,
    )
    if current_platform == "Windows":
        from .windows import WindowsServiceManager

        return WindowsServiceManager(context)
    if current_platform == "Linux":
        from .systemd import SystemdServiceManager

        return SystemdServiceManager(context)
    if current_platform == "Darwin":
        from .launchd import LaunchdServiceManager

        return LaunchdServiceManager(context)
    raise RuntimeError(f"Service management is not supported on {current_platform}.")
