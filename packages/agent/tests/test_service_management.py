from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iot_agent.config import AgentSettings
from iot_agent.service.launchd import LaunchdServiceManager
from iot_agent.service.manager import build_service_context, build_service_manager
from iot_agent.service.models import ServiceDefinition, ServiceState, ServiceStatus
from iot_agent.service.systemd import SystemdServiceManager


def test_cli_service_install_uses_service_manager(tmp_path, mocker) -> None:
    fake_manager = mocker.Mock()
    fake_manager.install.return_value = "Installed the service."
    mocker.patch(
        "iot_agent.cli._service_manager",
        return_value=(AgentSettings.model_validate({"path_profile": "production"}), tmp_path / "agent.toml", fake_manager),
    )

    from iot_agent.cli import app

    result = CliRunner().invoke(app, ["service", "install", "--config", str(tmp_path / "agent.toml")])

    assert result.exit_code == 0, result.output
    assert "Installed the service." in result.output
    fake_manager.install.assert_called_once_with()


def test_windows_service_manager_install_persists_config_path(tmp_path, mocker) -> None:
    settings = AgentSettings.model_validate(
        {
            "path_profile": "production",
            "data_dir": tmp_path / "data",
            "log_dir": tmp_path / "logs",
            "temp_dir": tmp_path / "tmp",
            "security_state_dir": tmp_path / "security",
            "runtime_database_path": tmp_path / "data" / "iot-agent.sqlite3",
        }
    )
    fake_win32serviceutil = mocker.Mock()
    mocker.patch("iot_agent.service.windows.WindowsServiceManager._serviceutil", return_value=fake_win32serviceutil)
    fake_service_class = object()
    mocker.patch("iot_agent.windows_service.create_windows_service_class", return_value=fake_service_class)
    persist_config = mocker.patch("iot_agent.windows_service.set_windows_service_config_path")

    from iot_agent.service.windows import WindowsServiceManager

    manager = WindowsServiceManager(
        build_service_context(
            settings,
            config_path=tmp_path / "iot-agent.toml",
            scope="system",
            platform_system="Windows",
        )
    )

    message = manager.install()

    assert message == "Installed Windows service 'IoTAgentService'."
    fake_win32serviceutil.HandleCommandLine.assert_called_once_with(
        fake_service_class,
        argv=["iot-agent-windows-service", "--startup", "delayed", "install"],
    )
    persist_config.assert_called_once_with(tmp_path / "iot-agent.toml")


def test_cli_service_status_prints_current_state(tmp_path, mocker) -> None:
    fake_manager = mocker.Mock()
    fake_manager.status.return_value = ServiceStatus(
        state=ServiceState.RUNNING,
        detail="Managing a test service.",
    )
    mocker.patch(
        "iot_agent.cli._service_manager",
        return_value=(AgentSettings.model_validate({"path_profile": "production"}), tmp_path / "agent.toml", fake_manager),
    )

    from iot_agent.cli import app

    result = CliRunner().invoke(app, ["service", "status"])

    assert result.exit_code == 0, result.output
    assert "State: running" in result.output
    assert "Managing a test service." in result.output


def test_cli_print_definition_streams_definition_content(tmp_path, mocker) -> None:
    fake_manager = mocker.Mock()
    fake_manager.definition.return_value = ServiceDefinition(
        format_name="systemd",
        path=tmp_path / "iot-agent.service",
        content="[Unit]\nDescription=IoT Agent\n",
    )
    mocker.patch(
        "iot_agent.cli._service_manager",
        return_value=(AgentSettings.model_validate({"path_profile": "production"}), tmp_path / "agent.toml", fake_manager),
    )

    from iot_agent.cli import app

    result = CliRunner().invoke(app, ["service", "print-definition"])

    assert result.exit_code == 0, result.output
    assert "# Path:" in result.output
    assert "Description=IoT Agent" in result.output


def test_cli_config_write_default_omits_schema_header(tmp_path) -> None:
    target_path = tmp_path / "iot-agent.toml"

    from iot_agent.cli import app

    result = CliRunner().invoke(app, ["config", "write-default", "--config", str(target_path)])

    assert result.exit_code == 0, result.output
    content = target_path.read_text(encoding="utf-8")
    assert not content.startswith("#:schema")
    assert '[paths]\nprofile = "production"' in content


def test_build_service_manager_rejects_windows_user_scope() -> None:
    with pytest.raises(RuntimeError, match="only support the system scope"):
        build_service_manager(
            AgentSettings.model_validate({"path_profile": "production"}),
            config_path=Path("C:/tmp/iot-agent.toml"),
            scope="user",
            platform_system="Windows",
        )


def test_systemd_definition_includes_serve_command(tmp_path) -> None:
    settings = AgentSettings.model_validate(
        {
            "path_profile": "production",
            "data_dir": tmp_path / "data",
            "log_dir": tmp_path / "logs",
            "temp_dir": tmp_path / "tmp",
            "security_state_dir": tmp_path / "security",
            "runtime_database_path": tmp_path / "data" / "iot-agent.sqlite3",
        }
    )
    manager = SystemdServiceManager(
        build_service_context(
            settings,
            config_path=tmp_path / "iot-agent.toml",
            scope="system",
            platform_system="Linux",
        )
    )

    definition = manager.definition()

    assert definition.format_name == "systemd"
    assert "ExecStart=" in definition.content
    assert "-m iot_agent serve --config" in definition.content
    assert "Restart=on-failure" in definition.content


def test_systemd_install_writes_unit_and_enables_service(tmp_path, mocker) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    settings = AgentSettings.model_validate(
        {
            "path_profile": "production",
            "data_dir": tmp_path / "data",
            "log_dir": tmp_path / "logs",
            "temp_dir": tmp_path / "tmp",
            "security_state_dir": tmp_path / "security",
            "runtime_database_path": tmp_path / "data" / "iot-agent.sqlite3",
        }
    )
    manager = SystemdServiceManager(
        build_service_context(
            settings,
            config_path=tmp_path / "iot-agent.toml",
            scope="system",
            platform_system="Linux",
        ),
        runner=runner,
    )
    unit_path = tmp_path / "iot-agent.service"
    mocker.patch.object(SystemdServiceManager, "_unit_path", return_value=unit_path)

    message = manager.install()

    assert unit_path.exists()
    assert message == f"Installed systemd unit at {unit_path}."
    assert commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "enable", "iot-agent.service"),
    ]


def test_launchd_definition_includes_expected_label_and_args(tmp_path) -> None:
    settings = AgentSettings.model_validate(
        {
            "path_profile": "production",
            "data_dir": tmp_path / "data",
            "log_dir": tmp_path / "logs",
            "temp_dir": tmp_path / "tmp",
            "security_state_dir": tmp_path / "security",
            "runtime_database_path": tmp_path / "data" / "iot-agent.sqlite3",
        }
    )
    manager = LaunchdServiceManager(
        build_service_context(
            settings,
            config_path=tmp_path / "iot-agent.toml",
            scope="user",
            platform_system="Darwin",
        )
    )

    definition = manager.definition()
    payload = plistlib.loads(definition.content.encode("utf-8"))

    assert definition.format_name == "launchd"
    assert payload["Label"] == "io.iot-agent.service"
    assert payload["ProgramArguments"][-2:] == ["--config", str(tmp_path / "iot-agent.toml")]


def test_launchd_install_writes_plist_and_bootstraps_job(tmp_path, mocker) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        if len(command) > 1 and command[1] == "bootout":
            raise RuntimeError("not loaded")
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    settings = AgentSettings.model_validate(
        {
            "path_profile": "production",
            "data_dir": tmp_path / "data",
            "log_dir": tmp_path / "logs",
            "temp_dir": tmp_path / "tmp",
            "security_state_dir": tmp_path / "security",
            "runtime_database_path": tmp_path / "data" / "iot-agent.sqlite3",
        }
    )
    manager = LaunchdServiceManager(
        build_service_context(
            settings,
            config_path=tmp_path / "iot-agent.toml",
            scope="user",
            platform_system="Darwin",
        ),
        runner=runner,
    )
    plist_path = tmp_path / "io.iot-agent.service.plist"
    mocker.patch.object(LaunchdServiceManager, "_plist_path", return_value=plist_path)
    mocker.patch.object(LaunchdServiceManager, "_domain_target", return_value="gui/501")

    message = manager.install()

    assert plist_path.exists()
    assert message == f"Installed launchd plist at {plist_path}."
    assert commands == [
        ("launchctl", "bootout", "gui/501/io.iot-agent.service"),
        ("launchctl", "bootstrap", "gui/501", str(plist_path)),
        ("launchctl", "enable", "gui/501/io.iot-agent.service"),
    ]
