from __future__ import annotations

import socket
import sys
from typing import Any

from .config import AgentSettings, load_settings
from .server import AgentServerController
from .version import SERVICE_NAME

WINDOWS_SERVICE_NAME = "IoTAgentService"
WINDOWS_SERVICE_DISPLAY_NAME = SERVICE_NAME
WINDOWS_SERVICE_DESCRIPTION = "Secure local gateway service for IoT devices and the IoT Agent tray."


def main(argv: list[str] | None = None) -> None:
    _run_service_cli(argv or sys.argv)


def _run_service_cli(argv: list[str]) -> None:
    servicemanager, _, _, win32serviceutil = _import_pywin32_service_modules()
    service_class = create_windows_service_class()
    if len(argv) > 1:
        win32serviceutil.HandleCommandLine(service_class, argv=argv)
        return
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(service_class)
    servicemanager.StartServiceCtrlDispatcher()


def create_windows_service_class(*, settings: AgentSettings | None = None):
    service_settings = settings or load_settings()
    _, win32event, win32service, win32serviceutil = _import_pywin32_service_modules()

    class IoTAgentWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = WINDOWS_SERVICE_NAME
        _svc_display_name_ = WINDOWS_SERVICE_DISPLAY_NAME
        _svc_description_ = WINDOWS_SERVICE_DESCRIPTION

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self._controller = AgentServerController.from_settings(service_settings)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._controller.request_shutdown()
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self) -> None:
            servicemanager, _, _, _ = _import_pywin32_service_modules()
            servicemanager.LogInfoMsg(f"{WINDOWS_SERVICE_DISPLAY_NAME} is starting.")
            try:
                self._controller.run()
            except Exception as exc:  # pragma: no cover - defensive integration path
                servicemanager.LogErrorMsg(
                    f"{WINDOWS_SERVICE_DISPLAY_NAME} failed: {type(exc).__name__}: {exc}"
                )
                raise
            finally:
                servicemanager.LogInfoMsg(f"{WINDOWS_SERVICE_DISPLAY_NAME} has stopped.")

    return IoTAgentWindowsService


def _import_pywin32_service_modules() -> tuple[Any, Any, Any, Any]:
    if sys.platform != "win32":
        raise RuntimeError("Windows service hosting is only available on Windows.")

    import servicemanager  # type: ignore[import-not-found]
    import win32event  # type: ignore[import-not-found]
    import win32service  # type: ignore[import-not-found]
    import win32serviceutil  # type: ignore[import-not-found]

    return servicemanager, win32event, win32service, win32serviceutil


def windows_service_endpoint() -> str:
    return f"{WINDOWS_SERVICE_NAME}@{socket.gethostname()}"
