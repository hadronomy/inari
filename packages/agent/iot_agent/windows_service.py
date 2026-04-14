from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path
from typing import Callable
from typing import Any

from .config import AgentSettings, load_settings
from .server import AgentServerController
from .service.models import DEFAULT_SERVICE_IDENTITY

WINDOWS_SERVICE_NAME = DEFAULT_SERVICE_IDENTITY.windows_name
WINDOWS_SERVICE_DISPLAY_NAME = DEFAULT_SERVICE_IDENTITY.display_name
WINDOWS_SERVICE_DESCRIPTION = DEFAULT_SERVICE_IDENTITY.description
WINDOWS_SERVICE_CONFIG_OPTION = "ConfigPath"


def main(argv: list[str] | None = None) -> None:
    _run_service_cli(argv or sys.argv)


def _run_service_cli(argv: list[str]) -> None:
    config_path, service_argv = _parse_service_cli_args(argv)
    servicemanager, _, _, win32serviceutil = _import_pywin32_service_modules()
    service_class = create_windows_service_class()
    if len(service_argv) > 1:
        win32serviceutil.HandleCommandLine(service_class, argv=service_argv)
        if config_path is not None and _mutates_service_registration(service_argv):
            set_windows_service_config_path(config_path)
        return
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(service_class)
    servicemanager.StartServiceCtrlDispatcher()


def create_windows_service_class(
    *,
    settings: AgentSettings | None = None,
    config_path: Path | str | None = None,
):
    settings_loader = _build_settings_loader(settings=settings, config_path=config_path)
    _, win32event, win32service, win32serviceutil = _import_pywin32_service_modules()

    class IoTAgentWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = WINDOWS_SERVICE_NAME
        _svc_display_name_ = WINDOWS_SERVICE_DISPLAY_NAME
        _svc_description_ = WINDOWS_SERVICE_DESCRIPTION

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self._controller = AgentServerController.from_settings(settings_loader())

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

    IoTAgentWindowsService.__module__ = __name__
    globals()["IoTAgentWindowsService"] = IoTAgentWindowsService
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


def get_windows_service_config_path() -> Path | None:
    _, _, _, win32serviceutil = _import_pywin32_service_modules()
    raw_value = win32serviceutil.GetServiceCustomOption(
        WINDOWS_SERVICE_NAME,
        WINDOWS_SERVICE_CONFIG_OPTION,
        None,
    )
    if raw_value in {None, ""}:
        return None
    return Path(str(raw_value)).expanduser().resolve()


def set_windows_service_config_path(config_path: Path | str) -> None:
    _, _, _, win32serviceutil = _import_pywin32_service_modules()
    resolved_path = Path(config_path).expanduser().resolve()
    win32serviceutil.SetServiceCustomOption(
        WINDOWS_SERVICE_NAME,
        WINDOWS_SERVICE_CONFIG_OPTION,
        str(resolved_path),
    )


def _load_service_settings(config_path: Path | str | None = None) -> AgentSettings:
    resolved_config_path = Path(config_path).expanduser().resolve() if config_path is not None else None
    if resolved_config_path is None:
        resolved_config_path = get_windows_service_config_path()
    if resolved_config_path is not None and not resolved_config_path.exists():
        return AgentSettings.model_validate({"path_profile": "production"})
    return load_settings(config_path=resolved_config_path)


def _build_settings_loader(
    *,
    settings: AgentSettings | None,
    config_path: Path | str | None,
) -> Callable[[], AgentSettings]:
    if settings is not None:
        return lambda: settings
    return lambda: _load_service_settings(config_path=config_path)


def _mutates_service_registration(argv: list[str]) -> bool:
    return any(argument in {"install", "update"} for argument in argv[1:])


if sys.platform == "win32":  # pragma: no branch - Windows-only integration path.
    try:
        create_windows_service_class()
    except Exception:
        pass
