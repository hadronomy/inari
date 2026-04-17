from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from .manager import (
    ServiceContext,
    ensure_service_config_file,
    validate_service_config_file,
)
from .models import ServiceDefinition, ServiceScope, ServiceState, ServiceStatus


@dataclass(slots=True)
class WindowsServiceManager:
    context: ServiceContext

    @property
    def identity(self):
        return self.context.identity

    @property
    def scope(self) -> ServiceScope:
        return "system"

    def install(self) -> str:
        win32serviceutil = self._serviceutil()
        from ..windows_service import (
            create_windows_service_class,
            set_windows_service_config_path,
        )

        self._ensure_parent_directories()
        config_created = ensure_service_config_file(self.context.config_path)
        service_class = create_windows_service_class()
        win32serviceutil.HandleCommandLine(
            service_class,
            argv=[
                "iot-agent-windows-service",
                "--startup",
                "delayed",
                "install",
            ],
        )
        set_windows_service_config_path(self.context.config_path)
        message = f"Installed Windows service {self.identity.windows_name!r}."
        if config_created:
            message += f" Wrote default config to {self.context.config_path}."
        return message

    def uninstall(self) -> str:
        win32serviceutil = self._serviceutil()
        from ..windows_service import create_windows_service_class

        if self.status().state in {
            ServiceState.RUNNING,
            ServiceState.STARTING,
            ServiceState.STOPPING,
        }:
            self.stop()
            self._wait_for_state(ServiceState.STOPPED, timeout_seconds=20.0)
        service_class = create_windows_service_class()
        win32serviceutil.HandleCommandLine(
            service_class,
            argv=["iot-agent-windows-service", "remove"],
        )
        return f"Removed Windows service {self.identity.windows_name!r}."

    def start(self) -> str:
        validate_service_config_file(self.context.config_path)
        self._serviceutil().StartService(self.identity.windows_name)
        return f"Started Windows service {self.identity.windows_name!r}."

    def stop(self) -> str:
        self._serviceutil().StopService(self.identity.windows_name)
        return f"Stopped Windows service {self.identity.windows_name!r}."

    def restart(self) -> str:
        validate_service_config_file(self.context.config_path)
        if self.status().state in {
            ServiceState.RUNNING,
            ServiceState.STARTING,
            ServiceState.STOPPING,
        }:
            self.stop()
            self._wait_for_state(ServiceState.STOPPED, timeout_seconds=20.0)
        self.start()
        return f"Restarted Windows service {self.identity.windows_name!r}."

    def status(self) -> ServiceStatus:
        win32service, win32serviceutil = self._service_modules()
        try:
            raw_status = win32serviceutil.QueryServiceStatus(self.identity.windows_name)
        except Exception as exc:
            return ServiceStatus(
                state=ServiceState.NOT_INSTALLED,
                detail=str(exc),
            )
        raw_state = int(raw_status[1])
        state = {
            int(win32service.SERVICE_RUNNING): ServiceState.RUNNING,
            int(win32service.SERVICE_STOPPED): ServiceState.STOPPED,
            int(win32service.SERVICE_START_PENDING): ServiceState.STARTING,
            int(win32service.SERVICE_STOP_PENDING): ServiceState.STOPPING,
        }.get(raw_state, ServiceState.UNKNOWN)
        return ServiceStatus(
            state=state,
            detail=f"Managing Windows service {self.identity.windows_name!r}.",
        )

    def _wait_for_state(self, desired: ServiceState, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.status().state is desired:
                return
            time.sleep(0.5)
        raise RuntimeError(f"Timed out while waiting for {desired.value}.")

    def definition(self) -> ServiceDefinition:
        content = "\n".join(
            [
                f"service_name = {self.identity.windows_name}",
                f"display_name = {self.identity.display_name}",
                f"description = {self.identity.description}",
                f"host_executable = {Path(sys.executable).name}",
                "service_entrypoint = python -m iot_agent.windows_service",
                f"config_path = {self.context.config_path}",
                "startup = delayed-auto",
            ]
        )
        return ServiceDefinition(
            format_name="windows-service",
            content=content + "\n",
        )

    def _ensure_parent_directories(self) -> None:
        for path in (
            self.context.config_path.parent,
            self.context.settings.data_dir,
            self.context.settings.log_dir,
            self.context.settings.temp_dir,
            self.context.settings.security_state_dir,
            self.context.settings.runtime_database_path.parent
            if self.context.settings.runtime_database_path is not None
            else None,
        ):
            if path is not None:
                Path(path).mkdir(parents=True, exist_ok=True)

    def _serviceutil(self) -> Any:
        _, win32serviceutil = self._service_modules()
        return win32serviceutil

    def _service_modules(self) -> tuple[Any, Any]:
        from ..windows_service import _import_pywin32_service_modules

        _, _, win32service, win32serviceutil = _import_pywin32_service_modules()
        return win32service, win32serviceutil
