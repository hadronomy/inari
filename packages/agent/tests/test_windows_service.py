from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from typing import Any, cast

from inari.config import AgentSettings


def test_service_cli_uses_handle_command_line_for_management_commands(
    mocker, tmp_path
) -> None:
    fake_win32serviceutil = SimpleNamespace(
        HandleCommandLine=mocker.Mock(),
        SetServiceCustomOption=mocker.Mock(),
    )
    fake_modules = (
        SimpleNamespace(
            Initialize=mocker.Mock(),
            PrepareToHostSingle=mocker.Mock(),
            StartServiceCtrlDispatcher=mocker.Mock(),
        ),
        SimpleNamespace(CreateEvent=mocker.Mock(), SetEvent=mocker.Mock()),
        SimpleNamespace(SERVICE_STOP_PENDING=3),
        fake_win32serviceutil,
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint._import_pywin32_service_modules",
        return_value=fake_modules,
    )
    mocked_service_class = object()
    mocker.patch(
        "inari.host_service.windows_entrypoint.create_windows_service_class",
        return_value=mocked_service_class,
    )

    from inari.host_service.windows_entrypoint import _run_service_cli

    _run_service_cli(
        [
            "inari-windows-service",
            "--config",
            str(tmp_path / "agent.toml"),
            "install",
        ]
    )

    fake_win32serviceutil.HandleCommandLine.assert_called_once_with(
        mocked_service_class,
        argv=["inari-windows-service", "install"],
    )
    fake_win32serviceutil.SetServiceCustomOption.assert_called_once_with(
        "InariAgent",
        "ConfigPath",
        str((tmp_path / "agent.toml").resolve()),
    )


def test_service_custom_option_uses_write_api_and_read_only_registry_access(
    mocker, tmp_path
) -> None:
    fake_servicemanager = SimpleNamespace(
        LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock()
    )
    fake_win32event = SimpleNamespace(
        CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock()
    )
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)
    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=type(
            "FakeServiceFramework", (), {"__init__": lambda self, args: None}
        ),
        SetServiceCustomOption=mocker.Mock(),
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint._import_pywin32_service_modules",
        return_value=(
            fake_servicemanager,
            fake_win32event,
            fake_win32service,
            fake_win32serviceutil,
        ),
    )
    registry_key = mocker.MagicMock()
    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(),
        KEY_READ=0x20019,
        OpenKey=mocker.Mock(return_value=registry_key),
        QueryValueEx=mocker.Mock(
            return_value=(str((tmp_path / "agent.toml").resolve()), 1)
        ),
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint.importlib.import_module",
        return_value=fake_winreg,
    )

    from inari.host_service.windows_entrypoint import (
        get_windows_service_config_path,
        set_windows_service_config_path,
    )

    config_path = tmp_path / "agent.toml"
    set_windows_service_config_path(config_path)

    fake_win32serviceutil.SetServiceCustomOption.assert_called_once_with(
        "InariAgent",
        "ConfigPath",
        str(config_path.resolve()),
    )
    assert get_windows_service_config_path() == config_path.resolve()
    fake_winreg.OpenKey.assert_called_once_with(
        fake_winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Services\InariAgent\Parameters",
        0,
        fake_winreg.KEY_READ,
    )
    fake_winreg.QueryValueEx.assert_called_once_with(
        registry_key.__enter__.return_value,
        "ConfigPath",
    )


def test_missing_service_config_path_does_not_require_registry_write_access(
    mocker,
) -> None:
    fake_win32serviceutil = SimpleNamespace(
        GetServiceCustomOption=mocker.Mock(
            side_effect=PermissionError("RegCreateKey requires write access")
        )
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint._import_pywin32_service_modules",
        return_value=(
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            fake_win32serviceutil,
        ),
    )
    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(),
        KEY_READ=0x20019,
        OpenKey=mocker.Mock(side_effect=FileNotFoundError),
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint.importlib.import_module",
        return_value=fake_winreg,
    )

    from inari.host_service.windows_entrypoint import (
        WINDOWS_SERVICE_NAME,
        get_windows_service_config_path,
    )

    assert get_windows_service_config_path() is None
    fake_win32serviceutil.GetServiceCustomOption.assert_not_called()
    fake_winreg.OpenKey.assert_called_once_with(
        fake_winreg.HKEY_LOCAL_MACHINE,
        rf"SYSTEM\CurrentControlSet\Services\{WINDOWS_SERVICE_NAME}\Parameters",
        0,
        fake_winreg.KEY_READ,
    )


def test_bootstrap_log_path_does_not_read_the_service_registry(
    mocker, tmp_path
) -> None:
    log_dir = tmp_path / "logs"
    mocker.patch(
        "inari.host_service.windows_entrypoint.resolve_default_path_bundle",
        return_value=SimpleNamespace(log_dir=log_dir),
    )
    registry_reader = mocker.patch(
        "inari.host_service.windows_entrypoint.get_windows_service_config_path",
        side_effect=PermissionError("the bootstrap log must not read the registry"),
    )

    from inari.host_service.windows_entrypoint import _bootstrap_log_path

    assert _bootstrap_log_path() == log_dir / "service-bootstrap.log"
    assert log_dir.is_dir()
    registry_reader.assert_not_called()


def test_service_class_requests_shutdown_when_stopped(mocker) -> None:
    fake_servicemanager = SimpleNamespace(
        LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock()
    )
    fake_win32event = SimpleNamespace(
        CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock()
    )
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)

    class FakeServiceFramework:
        def __init__(self, args):
            self.args = args

        def ReportServiceStatus(self, status_code):
            self.status_code = status_code

    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=FakeServiceFramework,
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint._import_pywin32_service_modules",
        return_value=(
            fake_servicemanager,
            fake_win32event,
            fake_win32service,
            fake_win32serviceutil,
        ),
    )
    fake_controller = SimpleNamespace(
        run=mocker.Mock(),
        request_shutdown=mocker.Mock(),
        container=SimpleNamespace(standalone_trust_service=None),
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint.AgentServerController.from_settings",
        return_value=fake_controller,
    )

    from inari.host_service.windows_entrypoint import create_windows_service_class

    service_class = create_windows_service_class(settings=AgentSettings())
    service = service_class(["inari-windows-service"])
    service._controller = fake_controller
    service.SvcStop()

    assert service.status_code == 3
    fake_controller.request_shutdown.assert_called_once_with()
    fake_win32event.SetEvent.assert_called_once_with("event")


def test_service_class_uses_python_module_host(mocker) -> None:
    fake_servicemanager = SimpleNamespace(
        LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock()
    )
    fake_win32event = SimpleNamespace(
        CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock()
    )
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)
    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=type(
            "FakeServiceFramework", (), {"__init__": lambda self, args: None}
        ),
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint._import_pywin32_service_modules",
        return_value=(
            fake_servicemanager,
            fake_win32event,
            fake_win32service,
            fake_win32serviceutil,
        ),
    )

    from inari.host_service.windows_entrypoint import create_windows_service_class

    service_class = create_windows_service_class(settings=AgentSettings())

    assert Path(service_class._exe_name_).name.startswith("python")
    assert service_class._exe_args_ == "-m inari.host_service.windows_entrypoint"


def test_service_class_builds_controller_during_run(mocker) -> None:
    fake_servicemanager = SimpleNamespace(
        LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock()
    )
    fake_win32event = SimpleNamespace(
        CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock()
    )

    class FakeServiceFramework:
        def __init__(self, args):
            self.args = args
            self.reported_statuses = []

        def ReportServiceStatus(
            self, status_code, waitHint=5000, win32ExitCode=0, svcExitCode=0
        ):
            self.reported_statuses.append(
                (status_code, waitHint, win32ExitCode, svcExitCode)
            )

    fake_win32service = SimpleNamespace(
        SERVICE_STOP_PENDING=3,
        SERVICE_START_PENDING=2,
        SERVICE_RUNNING=4,
        SERVICE_STOPPED=1,
    )
    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=FakeServiceFramework,
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint._import_pywin32_service_modules",
        return_value=(
            fake_servicemanager,
            fake_win32event,
            fake_win32service,
            fake_win32serviceutil,
        ),
    )
    mocker.patch("inari.host_service.windows_entrypoint._write_bootstrap_log")
    mocker.patch(
        "inari.host_service.windows_entrypoint.get_windows_service_config_path",
        return_value=None,
    )
    fake_controller = SimpleNamespace(
        container=SimpleNamespace(standalone_trust_service=None),
        run=mocker.Mock(),
        request_shutdown=mocker.Mock(),
    )
    controller_factory = mocker.patch(
        "inari.host_service.windows_entrypoint.AgentServerController.from_settings",
        return_value=fake_controller,
    )

    from inari.host_service.windows_entrypoint import create_windows_service_class

    service_class = create_windows_service_class(settings=AgentSettings())
    service = service_class(["inari-windows-service"])

    controller_factory.assert_not_called()

    service.SvcDoRun()

    controller_factory.assert_called_once()
    assert (2, 20000, 0, 0) in service.reported_statuses
    assert (4, 5000, 0, 0) in service.reported_statuses
    fake_controller.run.assert_called_once_with()


def test_windows_service_entrypoint_requires_windows(monkeypatch) -> None:
    from inari.host_service.windows_entrypoint import _import_pywin32_service_modules

    monkeypatch.setattr("sys.platform", "linux")
    import pytest

    with pytest.raises(RuntimeError, match="only available on Windows"):
        _import_pywin32_service_modules()


def test_load_service_settings_uses_production_defaults_without_registered_config(
    mocker,
) -> None:
    fake_servicemanager = SimpleNamespace(
        LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock()
    )
    fake_win32event = SimpleNamespace(
        CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock()
    )
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)
    fake_win32serviceutil = SimpleNamespace()
    mocker.patch(
        "inari.host_service.windows_entrypoint._import_pywin32_service_modules",
        return_value=(
            fake_servicemanager,
            fake_win32event,
            fake_win32service,
            fake_win32serviceutil,
        ),
    )
    mocker.patch(
        "inari.host_service.windows_entrypoint.get_windows_service_config_path",
        return_value=None,
    )

    from inari.host_service.windows_entrypoint import _load_service_settings

    settings = _load_service_settings()

    assert settings.path_profile == "production"


def test_module_entrypoint_invokes_main_when_run_as_script(
    mocker, monkeypatch, tmp_path
) -> None:
    fake_servicemanager = ModuleType("servicemanager")
    cast(Any, fake_servicemanager).Initialize = mocker.Mock()
    cast(Any, fake_servicemanager).PrepareToHostSingle = mocker.Mock()
    cast(Any, fake_servicemanager).StartServiceCtrlDispatcher = mocker.Mock()
    cast(Any, fake_servicemanager).LogInfoMsg = mocker.Mock()
    cast(Any, fake_servicemanager).LogErrorMsg = mocker.Mock()

    fake_win32event = ModuleType("win32event")
    cast(Any, fake_win32event).CreateEvent = mocker.Mock(return_value="event")
    cast(Any, fake_win32event).SetEvent = mocker.Mock()

    fake_win32service = ModuleType("win32service")
    cast(Any, fake_win32service).SERVICE_STOP_PENDING = 3

    fake_win32serviceutil = ModuleType("win32serviceutil")
    cast(Any, fake_win32serviceutil).ServiceFramework = type(
        "FakeServiceFramework", (), {"__init__": lambda self, args: None}
    )
    cast(Any, fake_win32serviceutil).HandleCommandLine = mocker.Mock()
    cast(Any, fake_win32serviceutil).SetServiceCustomOption = mocker.Mock()

    monkeypatch.setitem(sys.modules, "servicemanager", fake_servicemanager)
    monkeypatch.setitem(sys.modules, "win32event", fake_win32event)
    monkeypatch.setitem(sys.modules, "win32service", fake_win32service)
    monkeypatch.setitem(sys.modules, "win32serviceutil", fake_win32serviceutil)
    monkeypatch.delitem(
        sys.modules, "inari.host_service.windows_entrypoint", raising=False
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "inari.host_service.windows_entrypoint",
            "--config",
            str(tmp_path / "agent.toml"),
            "install",
        ],
    )

    runpy.run_module("inari.host_service.windows_entrypoint", run_name="__main__")

    cast(Any, fake_win32serviceutil).HandleCommandLine.assert_called_once()
