from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iot_agent.config import AgentSettings


def test_service_cli_uses_handle_command_line_for_management_commands(mocker, tmp_path) -> None:
    fake_win32serviceutil = SimpleNamespace(HandleCommandLine=mocker.Mock())
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


def test_service_class_includes_config_path_in_service_exe_args(mocker, tmp_path) -> None:
    fake_servicemanager = SimpleNamespace(LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock())
    fake_win32event = SimpleNamespace(CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock())
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)

    class FakeServiceFramework:
        def __init__(self, args):
            self.args = args

    fake_win32serviceutil = SimpleNamespace(ServiceFramework=FakeServiceFramework)
    mocker.patch(
        "iot_agent.windows_service._import_pywin32_service_modules",
        return_value=(fake_servicemanager, fake_win32event, fake_win32service, fake_win32serviceutil),
    )

    from iot_agent.windows_service import create_windows_service_class

    config_path = tmp_path / "iot-agent.toml"
    service_class = create_windows_service_class(
        settings=AgentSettings(),
        config_path=config_path,
    )

    assert Path(config_path).resolve().as_posix() in service_class._exe_args_.replace("\\", "/")


def test_service_class_requests_shutdown_when_stopped(mocker) -> None:
    fake_servicemanager = SimpleNamespace(LogInfoMsg=mocker.Mock(), LogErrorMsg=mocker.Mock())
    fake_win32event = SimpleNamespace(CreateEvent=mocker.Mock(return_value="event"), SetEvent=mocker.Mock())
    fake_win32service = SimpleNamespace(SERVICE_STOP_PENDING=3)

    class FakeServiceFramework:
        def __init__(self, args):
            self.args = args

        def ReportServiceStatus(self, status_code):
            self.status_code = status_code

    fake_win32serviceutil = SimpleNamespace(ServiceFramework=FakeServiceFramework)
    mocker.patch(
        "iot_agent.windows_service._import_pywin32_service_modules",
        return_value=(fake_servicemanager, fake_win32event, fake_win32service, fake_win32serviceutil),
    )
    fake_controller = SimpleNamespace(run=mocker.Mock(), request_shutdown=mocker.Mock())
    mocker.patch("iot_agent.windows_service.AgentServerController.from_settings", return_value=fake_controller)

    from iot_agent.windows_service import create_windows_service_class

    service_class = create_windows_service_class(settings=AgentSettings())
    service = service_class(["iot-agent-windows-service"])
    service.SvcStop()

    assert service.status_code == 3
    fake_controller.request_shutdown.assert_called_once_with()
    fake_win32event.SetEvent.assert_called_once_with("event")


def test_windows_service_entrypoint_requires_windows(monkeypatch) -> None:
    from iot_agent.windows_service import _import_pywin32_service_modules

    monkeypatch.setattr("sys.platform", "linux")
    import pytest

    with pytest.raises(RuntimeError, match="only available on Windows"):
        _import_pywin32_service_modules()
