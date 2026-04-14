from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path
from typing import Any

from .config import AgentSettings, load_settings
from .server import AgentServerController
from .service.models import DEFAULT_SERVICE_IDENTITY

WINDOWS_SERVICE_NAME = DEFAULT_SERVICE_IDENTITY.windows_name
WINDOWS_SERVICE_DISPLAY_NAME = DEFAULT_SERVICE_IDENTITY.display_name
WINDOWS_SERVICE_DESCRIPTION = DEFAULT_SERVICE_IDENTITY.description


def main(argv: list[str] | None = None) -> None:
    _run_service_cli(argv or sys.argv)


def _run_service_cli(argv: list[str]) -> None:
    config_path, service_argv = _parse_service_cli_args(argv)
    servicemanager, _, _, win32serviceutil = _import_pywin32_service_modules()
    service_class = create_windows_service_class(config_path=config_path)
    if len(service_argv) > 1:
        win32serviceutil.HandleCommandLine(service_class, argv=service_argv)
        return
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(service_class)
    servicemanager.StartServiceCtrlDispatcher()


def create_windows_service_class(
    *,
    settings: AgentSettings | None = None,
    config_path: Path | str | None = None,
):
    resolved_config_path = Path(config_path).expanduser().resolve() if config_path is not None else None
    service_settings = settings or load_settings(config_path=resolved_config_path)
    _, win32event, win32service, win32serviceutil = _import_pywin32_service_modules()

    class IoTAgentWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = WINDOWS_SERVICE_NAME
        _svc_display_name_ = WINDOWS_SERVICE_DISPLAY_NAME
        _svc_description_ = WINDOWS_SERVICE_DESCRIPTION
        if resolved_config_path is not None:
            _exe_args_ = f'--config "{resolved_config_path}"'

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


def _parse_service_cli_args(argv: list[str]) -> tuple[Path | None, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    parsed, remaining = parser.parse_known_args(argv[1:])
    config_path = Path(parsed.config).expanduser().resolve() if parsed.config else None
    return config_path, [argv[0], *remaining]
