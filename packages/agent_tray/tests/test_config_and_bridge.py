from __future__ import annotations

import unittest
from pathlib import Path
import subprocess
from unittest.mock import patch

from iot_agent.models import SystemStatusResponse
from iot_agent.version import API_VERSION
from iot_agent_tray.app import AgentTrayApplication
from iot_agent_tray.bridge import (
    LaunchdAgentBridge,
    MonitorAgentBridge,
    SpawnedProcessAgentBridge,
    SystemdAgentBridge,
    UnsupportedServiceAgentBridge,
    build_control_bridge,
)
from iot_agent_tray.config import TraySettings
from iot_agent_tray.models import ControlMode, ControlSnapshot, LifecycleState
from iot_agent_tray.tray_host import TrayMenuEntry, create_tray_host


class TrayApplicationTests(unittest.TestCase):
    def test_run_bootstraps_host_and_background_threads(self) -> None:
        host = FakeTrayHost()
        application = AgentTrayApplication(
            TraySettings(),
            client=FakeTrayClient(),
            bridge=MonitorAgentBridge(),
            host=host,
        )

        application.run()

        self.assertTrue(host.run_called)
        self.assertEqual(host.initial_snapshot.title, "IoT Agent")
        self.assertTrue(any(entry.label == "Open API Docs" for entry in host.initial_menu_entries))
        self.assertEqual(len(application._threads), 2)
        application._stop_event.set()
        for thread in application._threads:
            thread.join(timeout=1.0)

    def test_setup_background_auto_starts_spawn_mode_when_enabled(self) -> None:
        bridge = FakeControlBridge()
        application = AgentTrayApplication(
            TraySettings(control_mode="spawn", auto_start_agent=True),
            client=FakeFailingTrayClient(),
            bridge=bridge,
            host=FakeTrayHost(),
        )

        application._setup_background()

        self.assertEqual(bridge.start_calls, 1)
        application._stop_event.set()
        for thread in application._threads:
            thread.join(timeout=1.0)

    def test_apply_snapshot_swallow_tray_host_update_errors(self) -> None:
        host = FailingTrayHost()
        application = AgentTrayApplication(
            TraySettings(),
            client=FakeTrayClient(),
            bridge=MonitorAgentBridge(),
            host=host,
        )
        snapshot = application.snapshot.with_error(
            control=ControlSnapshot(mode=ControlMode.MONITOR),
            message="x" * 256,
        )

        with self.assertLogs("iot_agent_tray.app", level="ERROR") as captured:
            application._apply_snapshot(snapshot)

        self.assertEqual(application.snapshot.last_error, "x" * 256)
        self.assertTrue(any("Failed to apply tray snapshot" in message for message in captured.output))

    def test_quit_tray_stops_host(self) -> None:
        host = FakeTrayHost()
        application = AgentTrayApplication(
            TraySettings(),
            client=FakeTrayClient(),
            bridge=MonitorAgentBridge(),
            host=host,
        )

        application._quit_tray()

        self.assertTrue(host.stopped)


class TraySettingsTests(unittest.TestCase):
    def test_settings_derive_related_agent_urls(self) -> None:
        settings = TraySettings(agent_api_base_url="http://localhost:7310/")

        self.assertEqual(settings.agent_api_base_url, "http://localhost:7310")
        self.assertEqual(settings.agent_docs_url, "http://localhost:7310/docs")
        self.assertEqual(settings.agent_devices_url, "http://localhost:7310/devices")
        self.assertEqual(settings.agent_jobs_url, "http://localhost:7310/jobs")
        self.assertEqual(settings.agent_events_url, "ws://localhost:7310/events")

    def test_create_tray_host_uses_qt_backend(self) -> None:
        host = create_tray_host(TraySettings())

        self.assertEqual(type(host).__name__, "QtTrayHost")


class SpawnedProcessBridgeTests(unittest.TestCase):
    def test_spawned_process_bridge_reports_stopped_before_first_launch(self) -> None:
        bridge = SpawnedProcessAgentBridge(TraySettings(control_mode="spawn"))

        state = bridge.query_state()

        self.assertEqual(state.mode, ControlMode.SPAWN)
        self.assertEqual(state.lifecycle, LifecycleState.STOPPED)
        self.assertTrue(state.can_start)

    def test_spawned_process_bridge_manages_process_lifecycle(self) -> None:
        created: list[FakeProcess] = []

        def process_factory(*args, **kwargs):
            process = FakeProcess()
            created.append(process)
            return process

        bridge = SpawnedProcessAgentBridge(
            TraySettings(control_mode="spawn"),
            process_factory=process_factory,
        )

        start_message = bridge.start()
        running = bridge.query_state()
        stop_message = bridge.stop()
        stopped = bridge.query_state()

        self.assertEqual(start_message, "Started the local agent process.")
        self.assertEqual(running.mode, ControlMode.SPAWN)
        self.assertEqual(running.lifecycle, LifecycleState.RUNNING)
        self.assertTrue(running.can_stop)
        self.assertEqual(stop_message, "Stopped the local agent process.")
        self.assertEqual(stopped.lifecycle, LifecycleState.STOPPED)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].terminated)

    def test_spawned_process_bridge_shutdown_stops_managed_process_by_default(self) -> None:
        created: list[FakeProcess] = []

        def process_factory(*args, **kwargs):
            process = FakeProcess()
            created.append(process)
            return process

        bridge = SpawnedProcessAgentBridge(
            TraySettings(control_mode="spawn"),
            process_factory=process_factory,
        )

        bridge.start()
        bridge.shutdown()

        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].terminated)
        self.assertEqual(bridge.query_state().lifecycle, LifecycleState.STOPPED)

    def test_spawned_process_bridge_prefers_same_interpreter_module_launch(self) -> None:
        bridge = SpawnedProcessAgentBridge(TraySettings(control_mode="spawn"))

        with patch("iot_agent_tray.bridge._supports_module_launch", return_value=True):
            command = bridge._resolve_launch_command()

        self.assertEqual(command, (bridge_module_sys_executable(), "-m", "iot_agent"))

    def test_spawned_process_bridge_falls_back_to_console_script(self) -> None:
        bridge = SpawnedProcessAgentBridge(TraySettings(control_mode="spawn"))

        with (
            patch("iot_agent_tray.bridge._supports_module_launch", return_value=False),
            patch("iot_agent_tray.bridge.shutil.which", side_effect=lambda name: "C:/bin/iot-agent.exe" if name == "iot-agent" else None),
        ):
            command = bridge._resolve_launch_command()

        self.assertEqual(command, ("C:/bin/iot-agent.exe",))

    def test_spawned_process_bridge_uses_uv_workspace_fallback(self) -> None:
        bridge = SpawnedProcessAgentBridge(
            TraySettings(control_mode="spawn"),
            working_directory=Path("C:/repo"),
        )

        with (
            patch("iot_agent_tray.bridge._supports_module_launch", return_value=False),
            patch("iot_agent_tray.bridge.shutil.which", side_effect=lambda name: "C:/bin/uv.exe" if name == "uv" else None),
            patch("iot_agent_tray.bridge._detect_agent_workspace", return_value=Path("C:/repo/packages/agent")),
        ):
            command = bridge._resolve_launch_command()

        self.assertEqual(
            command,
            ("C:/bin/uv.exe", "run", "--directory", "C:\\repo\\packages\\agent", "iot-agent"),
        )

    def test_build_control_bridge_uses_monitor_fallback(self) -> None:
        bridge = build_control_bridge(TraySettings(control_mode="monitor"))

        state = bridge.query_state()

        self.assertEqual(state.mode, ControlMode.MONITOR)
        self.assertEqual(state.lifecycle, LifecycleState.UNKNOWN)

    def test_build_control_bridge_selects_systemd_service_on_linux(self) -> None:
        bridge = build_control_bridge(
            TraySettings(control_mode="service", service_name="iot-agent.service"),
            platform_name="Linux",
        )

        self.assertIsInstance(bridge, SystemdAgentBridge)

    def test_build_control_bridge_selects_launchd_service_on_macos(self) -> None:
        bridge = build_control_bridge(
            TraySettings(control_mode="service", service_name="com.example.iot-agent"),
            platform_name="Darwin",
        )

        self.assertIsInstance(bridge, LaunchdAgentBridge)

    def test_build_control_bridge_reports_unsupported_service_platform(self) -> None:
        bridge = build_control_bridge(
            TraySettings(control_mode="service"),
            platform_name="FreeBSD",
        )

        self.assertIsInstance(bridge, UnsupportedServiceAgentBridge)
        self.assertEqual(bridge.query_state().lifecycle, LifecycleState.UNKNOWN)


class SystemdServiceBridgeTests(unittest.TestCase):
    def test_query_state_parses_active_system_service(self) -> None:
        commands: list[tuple[str, ...]] = []

        def runner(command):
            commands.append(tuple(command))
            return subprocess.CompletedProcess(list(command), 0, stdout="active\n", stderr="")

        bridge = SystemdAgentBridge(
            TraySettings(control_mode="service", service_name="iot-agent.service", service_scope="system"),
            runner=runner,
        )

        state = bridge.query_state()

        self.assertEqual(
            commands,
            [("systemctl", "show", "iot-agent.service", "--property=ActiveState", "--value")],
        )
        self.assertEqual(state.lifecycle, LifecycleState.RUNNING)
        self.assertIn("systemd service", state.detail)

    def test_query_state_uses_user_systemd_scope(self) -> None:
        commands: list[tuple[str, ...]] = []

        def runner(command):
            commands.append(tuple(command))
            return subprocess.CompletedProcess(list(command), 0, stdout="inactive\n", stderr="")

        bridge = SystemdAgentBridge(
            TraySettings(control_mode="service", service_name="iot-agent.service", service_scope="user"),
            runner=runner,
        )

        state = bridge.query_state()

        self.assertEqual(
            commands,
            [("systemctl", "--user", "show", "iot-agent.service", "--property=ActiveState", "--value")],
        )
        self.assertEqual(state.lifecycle, LifecycleState.STOPPED)
        self.assertIn("user", state.detail)


class LaunchdServiceBridgeTests(unittest.TestCase):
    def test_query_state_parses_running_launchd_job(self) -> None:
        commands: list[tuple[str, ...]] = []

        def runner(command):
            commands.append(tuple(command))
            return subprocess.CompletedProcess(list(command), 0, stdout="state = running\n", stderr="")

        bridge = LaunchdAgentBridge(
            TraySettings(control_mode="service", service_name="com.example.iot-agent", service_scope="user"),
            runner=runner,
        )

        state = bridge.query_state()

        self.assertEqual(commands, [("launchctl", "print", "gui/0/com.example.iot-agent")])
        self.assertEqual(state.lifecycle, LifecycleState.RUNNING)
        self.assertIn("launchd job", state.detail)

    def test_query_state_treats_missing_launchd_job_as_stopped(self) -> None:
        def runner(command):
            raise RuntimeError("Could not find service \"gui/0/com.example.iot-agent\"")

        bridge = LaunchdAgentBridge(
            TraySettings(control_mode="service", service_name="com.example.iot-agent", service_scope="user"),
            runner=runner,
        )

        state = bridge.query_state()

        self.assertEqual(state.lifecycle, LifecycleState.STOPPED)
        self.assertIn("launchd job", state.detail)


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeTrayHost:
    def __init__(self) -> None:
        self.run_called = False
        self.stopped = False
        self.initial_snapshot = None
        self.initial_menu_entries: list[TrayMenuEntry] = []
        self.updated_snapshot = None

    def run(self, *, snapshot, menu_entries, on_ready) -> None:
        self.run_called = True
        self.initial_snapshot = snapshot
        self.initial_menu_entries = list(menu_entries)
        on_ready()

    def update(self, *, snapshot, menu_entries) -> None:
        self.updated_snapshot = snapshot
        self.initial_menu_entries = list(menu_entries)

    def notify(self, *, title: str, message: str) -> None:
        return None

    def stop(self) -> None:
        self.stopped = True


class FailingTrayHost(FakeTrayHost):
    def update(self, *, snapshot, menu_entries) -> None:
        raise ValueError("Tray host update failed")


class FakeControlBridge:
    mode = ControlMode.SPAWN

    def __init__(self) -> None:
        self.start_calls = 0

    def query_state(self):
        return ControlSnapshot(
            mode=ControlMode.SPAWN,
            lifecycle=LifecycleState.STOPPED,
            detail="Ready to launch a local agent process.",
            can_start=True,
        )

    def start(self) -> str:
        self.start_calls += 1
        return "Started the local agent process."

    def shutdown(self) -> None:
        return None


class FakeTrayClient:
    def get_status(self) -> SystemStatusResponse:
        return SystemStatusResponse.model_validate(
            {
                "ok": True,
                "status": "healthy",
                "service": {"name": "IoT Agent", "version": API_VERSION},
                "devices": {
                    "count": 0,
                    "online_count": 0,
                    "offline_count": 0,
                    "kind_counts": {},
                    "default_device": None,
                },
                "queue": {
                    "total": 0,
                    "queued": 0,
                    "dispatched": 0,
                    "running": 0,
                    "retry_scheduled": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "cancelled": 0,
                },
                "supported_content_kinds": ["text"],
                "supported_device_commands": ["print_test_page"],
            }
        )

    def iter_live_updates(self, stop_event):
        return iter(())


class FakeFailingTrayClient(FakeTrayClient):
    def get_status(self) -> SystemStatusResponse:
        raise TimeoutError("timed out")


def bridge_module_sys_executable() -> str:
    import sys

    return sys.executable


if __name__ == "__main__":
    unittest.main()
