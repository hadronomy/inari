from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iot_agent.config import AgentSettings


def test_service_cli_uses_handle_command_line_for_management_commands(mocker, tmp_path) -> None:
    fake_win32serviceutil = SimpleNamespace(
        HandleCommandLine=mocker.Mock(),
        SetServiceCustomOption=mocker.Mock(),
        GetServiceCustomOption=mocker.Mock(return_value=None),
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
    mocker.patch("iot_agent.windows_service._import_pywin32_service_modules", return_value=fake_modules)
    mocked_service_class = object()
    mocker.patch("iot_agent.windows_service.create_windows_service_class", return_value=mocked_service_class)

    from iot_agent.windows_service import _run_service_cli

    _run_service_cli(["iot-agent-windows-service", "--config", str(tmp_path / "agent.toml"), "install"])

    fake_win32serviceutil.HandleCommandLine.assert_called_once_with(
        mocked_service_class,
        argv=["iot-agent-windows-service", "install"],
    )
    fake_win32serviceutil.SetServiceCustomOption.assert_called_once_with(
        "IoTAgentService",
        "ConfigPath",
        str((tmp_path / "agent.toml").resolve()),
    )


def test_service_custom_option_round_trip_uses_pywin32_storage(mocker, tmp_path) -> None:
    fake_servicemanager = SimpleNamespace(LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock())
    fake_win32event = SimpleNamespace(CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock())
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)
    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=type("FakeServiceFramework", (), {"__init__": lambda self, args: None}),
        SetServiceCustomOption=mocker.Mock(),
        GetServiceCustomOption=mocker.Mock(return_value=str((tmp_path / "agent.toml").resolve())),
    )
    mocker.patch(
        "iot_agent.windows_service._import_pywin32_service_modules",
        return_value=(fake_servicemanager, fake_win32event, fake_win32service, fake_win32serviceutil),
    )

    from iot_agent.windows_service import get_windows_service_config_path, set_windows_service_config_path

    config_path = tmp_path / "agent.toml"
    set_windows_service_config_path(config_path)

    fake_win32serviceutil.SetServiceCustomOption.assert_called_once_with(
        "IoTAgentService",
        "ConfigPath",
        str(config_path.resolve()),
    )
    assert get_windows_service_config_path() == config_path.resolve()


def test_service_class_requests_shutdown_when_stopped(mocker) -> None:
    fake_servicemanager = SimpleNamespace(LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock())
    fake_win32event = SimpleNamespace(CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock())
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)

    class FakeServiceFramework:
        def __init__(self, args):
            self.args = args

        def ReportServiceStatus(self, status_code):
            self.status_code = status_code

    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=FakeServiceFramework,
        GetServiceCustomOption=mocker.Mock(return_value=None),
    )
    mocker.patch(
        "iot_agent.windows_service._import_pywin32_service_modules",
        return_value=(fake_servicemanager, fake_win32event, fake_win32service, fake_win32serviceutil),
    )
    fake_controller = SimpleNamespace(run=mocker.Mock(), request_shutdown=mocker.Mock())
    mocker.patch("iot_agent.windows_service.AgentServerController.from_settings", return_value=fake_controller)

    from iot_agent.windows_service import create_windows_service_class

    service_class = create_windows_service_class(settings=AgentSettings())
    service = service_class(["iot-agent-windows-service"])
    service._controller = fake_controller
    service.SvcStop()

    assert service.status_code == 3
    fake_controller.request_shutdown.assert_called_once_with()
    fake_win32event.SetEvent.assert_called_once_with("event")


def test_service_class_uses_python_module_host(mocker) -> None:
    fake_servicemanager = SimpleNamespace(LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock())
    fake_win32event = SimpleNamespace(CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock())
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)
    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=type("FakeServiceFramework", (), {"__init__": lambda self, args: None}),
        GetServiceCustomOption=mocker.Mock(return_value=None),
    )
    mocker.patch(
        "iot_agent.windows_service._import_pywin32_service_modules",
        return_value=(fake_servicemanager, fake_win32event, fake_win32service, fake_win32serviceutil),
    )

    from iot_agent.windows_service import create_windows_service_class

    service_class = create_windows_service_class(settings=AgentSettings())

    assert service_class._exe_name_.endswith("python.exe")
    assert service_class._exe_args_ == "-m iot_agent.windows_service"


def test_service_class_builds_controller_during_run(mocker) -> None:
    fake_servicemanager = SimpleNamespace(LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock())
    fake_win32event = SimpleNamespace(CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock())

    class FakeServiceFramework:
        def __init__(self, args):
            self.args = args
            self.reported_statuses = []

        def ReportServiceStatus(self, status_code, waitHint=5000, win32ExitCode=0, svcExitCode=0):
            self.reported_statuses.append((status_code, waitHint, win32ExitCode, svcExitCode))

    fake_win32service = SimpleNamespace(
        SERVICE_STOP_PENDING=3,
        SERVICE_START_PENDING=2,
        SERVICE_RUNNING=4,
        SERVICE_STOPPED=1,
    )
    fake_win32serviceutil = SimpleNamespace(
        ServiceFramework=FakeServiceFramework,
        GetServiceCustomOption=mocker.Mock(return_value=None),
    )
    mocker.patch(
        "iot_agent.windows_service._import_pywin32_service_modules",
        return_value=(fake_servicemanager, fake_win32event, fake_win32service, fake_win32serviceutil),
    )
    mocker.patch("iot_agent.windows_service._write_bootstrap_log")
    fake_controller = SimpleNamespace(run=mocker.Mock(), request_shutdown=mocker.Mock())
    controller_factory = mocker.patch(
        "iot_agent.windows_service.AgentServerController.from_settings",
        return_value=fake_controller,
    )

    from iot_agent.windows_service import create_windows_service_class

    service_class = create_windows_service_class(settings=AgentSettings())
    service = service_class(["iot-agent-windows-service"])

    controller_factory.assert_not_called()

    service.SvcDoRun()

    controller_factory.assert_called_once()
    assert (2, 20000, 0, 0) in service.reported_statuses
    assert (4, 5000, 0, 0) in service.reported_statuses
    fake_controller.run.assert_called_once_with()


def test_windows_service_entrypoint_requires_windows(monkeypatch) -> None:
    from iot_agent.windows_service import _import_pywin32_service_modules

    monkeypatch.setattr("sys.platform", "linux")
    import pytest

    with pytest.raises(RuntimeError, match="only available on Windows"):
        _import_pywin32_service_modules()


def test_load_service_settings_falls_back_to_production_defaults_when_config_missing(mocker, tmp_path) -> None:
    fake_servicemanager = SimpleNamespace(LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock())
    fake_win32event = SimpleNamespace(CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock())
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)
    fake_win32serviceutil = SimpleNamespace(
        GetServiceCustomOption=mocker.Mock(return_value=str(tmp_path / "missing.toml")),
    )
    mocker.patch(
        "iot_agent.windows_service._import_pywin32_service_modules",
        return_value=(fake_servicemanager, fake_win32event, fake_win32service, fake_win32serviceutil),
    )

    from iot_agent.windows_service import _load_service_settings

    settings = _load_service_settings()

    assert settings.path_profile == "production"
