from __future__ import annotations

from pathlib import Path
import subprocess


from inari.models import RuntimeEventResponse, SystemStatusResponse
from inari.version import API_VERSION
from inari_tray.app import AgentTrayApplication
from inari_tray.bridge import (
    LaunchdAgentBridge,
    MonitorAgentBridge,
    SpawnedProcessAgentBridge,
    SystemdAgentBridge,
    UnsupportedServiceAgentBridge,
    build_control_bridge,
)
from inari_tray.config import TraySettings
from inari_tray.models import (
    ControlMode,
    ControlSnapshot,
    LifecycleState,
    TrayLinks,
    TraySnapshot,
)
from inari_tray.qt_host import QtTrayHost
from inari_tray.tray_host import TrayMenuEntry, create_tray_host


def test_run_bootstraps_host_and_background_threads() -> None:
    host = FakeTrayHost()
    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=host,
    )

    application.run()

    assert host.run_called is True
    assert host.initial_snapshot.title == "Inari"
    assert any(entry.label == "Open API Docs" for entry in host.initial_menu_entries)
    assert any(
        entry.label == "Open Device Center" for entry in host.initial_menu_entries
    )
    assert len(application._threads) == 2
    application._stop_event.set()
    for thread in application._threads:
        thread.join(timeout=1.0)


def test_setup_background_auto_starts_spawn_mode_when_enabled() -> None:
    bridge = FakeControlBridge()
    application = AgentTrayApplication(
        TraySettings(control_mode="spawn", auto_start_agent=True),
        client=FakeFailingTrayClient(),
        bridge=bridge,
        host=FakeTrayHost(),
    )

    application._setup_background()

    assert bridge.start_calls == 1
    application._stop_event.set()
    for thread in application._threads:
        thread.join(timeout=1.0)


def test_setup_background_promotes_to_service_mode_when_api_is_reachable_and_service_is_running(
    mocker,
) -> None:
    bridge = FakeControlBridge()
    service_bridge = FakeServiceControlBridge()
    build_bridge = mocker.patch(
        "inari_tray.app.build_control_bridge", return_value=service_bridge
    )
    application = AgentTrayApplication(
        TraySettings(control_mode="spawn", auto_start_agent=True),
        client=FakeTrayClient(),
        bridge=bridge,
        host=FakeTrayHost(),
    )

    application._setup_background()

    assert application.bridge is service_bridge
    assert bridge.start_calls == 0
    build_bridge.assert_called()
    application._stop_event.set()
    for thread in application._threads:
        thread.join(timeout=1.0)


def test_refresh_snapshot_keeps_spawn_mode_when_service_is_not_running(mocker) -> None:
    bridge = FakeControlBridge()
    service_bridge = FakeServiceControlBridge(lifecycle=LifecycleState.STOPPED)
    mocker.patch("inari_tray.app.build_control_bridge", return_value=service_bridge)
    application = AgentTrayApplication(
        TraySettings(control_mode="spawn", auto_start_agent=True),
        client=FakeTrayClient(),
        bridge=bridge,
        host=FakeTrayHost(),
    )

    application._refresh_snapshot(notify_connection=False)

    assert application.bridge is bridge
    assert application.snapshot.control.mode is ControlMode.SPAWN


def test_apply_snapshot_swallow_tray_host_update_errors(caplog) -> None:
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

    with caplog.at_level("ERROR", logger="inari_tray.app"):
        application._apply_snapshot(snapshot)

    assert application.snapshot.last_error == "x" * 256
    assert any(
        "Failed to apply tray snapshot" in message for message in caplog.messages
    )


def test_quit_tray_stops_host() -> None:
    host = FakeTrayHost()
    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=host,
    )

    application._quit_tray()

    assert host.stopped is True


def test_open_device_center_uses_native_presenter() -> None:
    device_center = FakeDeviceCenter()
    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=FakeTrayHost(),
        device_center=device_center,
    )

    application._open_device_center()

    assert device_center.show_calls == 1


def test_lazy_device_center_receives_current_snapshot_on_creation() -> None:
    created: list[FakeDeviceCenter] = []

    def factory(*args, **kwargs):
        center = FakeDeviceCenter()
        created.append(center)
        return center

    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=FakeTrayHost(),
        device_center_factory=factory,
    )

    application._open_device_center()

    assert len(created) == 1
    assert created[0].snapshots[-1] == application.snapshot


def test_apply_snapshot_forwards_to_device_center() -> None:
    device_center = FakeDeviceCenter()
    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=FakeTrayHost(),
        device_center=device_center,
    )

    snapshot = application.snapshot.with_error(
        control=ControlSnapshot(mode=ControlMode.MONITOR),
        message="Connection refused",
    )
    application._apply_snapshot(snapshot)

    assert device_center.snapshots[-1] == snapshot


def test_forward_runtime_event_to_device_center() -> None:
    device_center = FakeDeviceCenter()
    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=FakeTrayHost(),
        device_center=device_center,
    )
    event = RuntimeEventResponse.model_validate(
        {
            "sequence": 4,
            "resource_kind": "device",
            "resource_id": "dev_printer",
            "event_type": "device.connected",
            "occurred_at": "2026-04-18T10:05:00Z",
            "payload": {"name": "Kitchen Printer"},
        }
    )

    application._forward_runtime_event(event)

    assert device_center.events == [event]


def test_quit_tray_closes_device_center() -> None:
    host = FakeTrayHost()
    device_center = FakeDeviceCenter()
    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=host,
        device_center=device_center,
    )

    application._quit_tray()

    assert device_center.closed is True
    assert host.stopped is True


def test_build_menu_does_not_render_error_row() -> None:
    application = AgentTrayApplication(
        TraySettings(),
        client=FakeTrayClient(),
        bridge=MonitorAgentBridge(),
        host=FakeTrayHost(),
    )

    baseline = application._build_menu(application.snapshot)
    errored = application._build_menu(
        application.snapshot.with_error(
            control=ControlSnapshot(mode=ControlMode.MONITOR),
            message="Connection refused",
        )
    )

    assert len(baseline) == len(errored)
    assert [entry.separator for entry in baseline] == [
        entry.separator for entry in errored
    ]
    assert not any(
        entry.label.startswith("Last error:")
        for entry in baseline
        if not entry.separator
    )
    assert not any(
        entry.label.startswith("Last error:")
        for entry in errored
        if not entry.separator
    )


def test_settings_derive_related_agent_urls() -> None:
    settings = TraySettings(agent_api_base_url="http://localhost:7310/")

    assert settings.agent_api_base_url == "http://localhost:7310"
    assert settings.agent_docs_url == "http://localhost:7310/docs"
    assert settings.agent_devices_url == "http://localhost:7310/devices"
    assert settings.agent_jobs_url == "http://localhost:7310/jobs"
    assert settings.agent_events_url == "ws://localhost:7310/events"


def test_settings_default_service_name_tracks_platform_defaults(monkeypatch) -> None:
    monkeypatch.setattr("inari.service.models.platform.system", lambda: "Linux")

    settings = TraySettings()

    assert settings.service_name == "inari.service"
    assert settings.service_scope == "system"


def test_create_tray_host_uses_qt_backend() -> None:
    host = create_tray_host(TraySettings())

    assert type(host).__name__ == "QtTrayHost"


def test_apply_update_keeps_menu_live_while_visible(mocker) -> None:
    host = QtTrayHost(title="Inari")
    host._tray_icon = FakeQtTrayIcon()
    host._menu = FakeQtMenu(visible=True)
    host._menu_actions = [object()]
    snapshot = _tray_snapshot()
    menu_entries = [TrayMenuEntry("Refresh Now")]

    mocker.patch("inari_tray.qt_host._image_to_qicon", return_value=object())
    mocker.patch("inari_tray.qt_host._menu_layout_matches", return_value=True)
    update_menu_actions = mocker.patch("inari_tray.qt_host._update_menu_actions")

    host._apply_update(snapshot, menu_entries)

    assert host._tray_icon.icon is not None
    assert host._tray_icon.tooltip == snapshot.tooltip
    update_menu_actions.assert_called_once_with(
        host._menu, host._menu_actions, menu_entries
    )


def test_apply_update_rebuilds_menu_when_layout_changes(mocker) -> None:
    host = QtTrayHost(title="Inari")
    host._tray_icon = FakeQtTrayIcon()
    host._menu = FakeQtMenu(visible=False)
    menu_entries = [TrayMenuEntry("Open Logs")]

    mocker.patch("inari_tray.qt_host._image_to_qicon", return_value=object())
    build_menu_actions = mocker.patch(
        "inari_tray.qt_host._build_menu_actions", return_value=["action"]
    )

    host._apply_update(_tray_snapshot(), menu_entries)

    assert host._menu_actions == ["action"]
    assert host._tray_icon.icon is not None
    assert host._tray_icon.tooltip == _tray_snapshot().tooltip
    build_menu_actions.assert_called_once_with(host._menu, menu_entries)


def test_spawned_process_bridge_reports_stopped_before_first_launch() -> None:
    bridge = SpawnedProcessAgentBridge(TraySettings(control_mode="spawn"))

    state = bridge.query_state()

    assert state.mode is ControlMode.SPAWN
    assert state.lifecycle is LifecycleState.STOPPED
    assert state.can_start is True


def test_spawned_process_bridge_manages_process_lifecycle() -> None:
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
    starting = bridge.query_state()
    bridge.mark_ready()
    running = bridge.query_state()
    stop_message = bridge.stop()
    stopped = bridge.query_state()

    assert start_message == "Started the local agent process."
    assert starting.mode is ControlMode.SPAWN
    assert starting.lifecycle is LifecycleState.STARTING
    assert starting.can_stop is True
    assert running.mode is ControlMode.SPAWN
    assert running.lifecycle is LifecycleState.RUNNING
    assert running.can_stop is True
    assert stop_message == "Stopped the local agent process."
    assert stopped.lifecycle is LifecycleState.STOPPED
    assert len(created) == 1
    assert created[0].terminated is True


def test_spawned_process_bridge_leaves_starting_state_after_grace_period() -> None:
    created: list[FakeProcess] = []
    now = 100.0

    def process_factory(*args, **kwargs):
        process = FakeProcess()
        created.append(process)
        return process

    def clock() -> float:
        return now

    bridge = SpawnedProcessAgentBridge(
        TraySettings(control_mode="spawn", startup_grace_period_seconds=5.0),
        process_factory=process_factory,
        clock=clock,
    )

    bridge.start()
    assert bridge.query_state().lifecycle is LifecycleState.STARTING

    now = 106.0

    assert bridge.query_state().lifecycle is LifecycleState.RUNNING


def test_spawned_process_bridge_shutdown_stops_managed_process_by_default() -> None:
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

    assert len(created) == 1
    assert created[0].terminated is True
    assert bridge.query_state().lifecycle is LifecycleState.STOPPED


def test_spawned_process_bridge_prefers_same_interpreter_module_launch(mocker) -> None:
    bridge = SpawnedProcessAgentBridge(TraySettings(control_mode="spawn"))

    mocker.patch("inari_tray.bridge._supports_module_launch", return_value=True)
    command = bridge._resolve_launch_command()

    assert command == (bridge_module_sys_executable(), "-m", "inari")


def test_spawned_process_bridge_falls_back_to_console_script(mocker) -> None:
    bridge = SpawnedProcessAgentBridge(TraySettings(control_mode="spawn"))

    mocker.patch("inari_tray.bridge._supports_module_launch", return_value=False)
    mocker.patch(
        "inari_tray.bridge.shutil.which",
        side_effect=lambda name: "C:/bin/inari.exe" if name == "inari" else None,
    )
    command = bridge._resolve_launch_command()

    assert command == ("C:/bin/inari.exe",)


def test_spawned_process_bridge_uses_uv_workspace_fallback(mocker) -> None:
    bridge = SpawnedProcessAgentBridge(
        TraySettings(control_mode="spawn"),
        working_directory=Path("C:/repo"),
    )

    mocker.patch("inari_tray.bridge._supports_module_launch", return_value=False)
    mocker.patch(
        "inari_tray.bridge.shutil.which",
        side_effect=lambda name: "C:/bin/uv.exe" if name == "uv" else None,
    )
    mocker.patch(
        "inari_tray.bridge._detect_agent_workspace",
        return_value=Path("C:/repo/packages/agent"),
    )
    command = bridge._resolve_launch_command()

    assert command == (
        "C:/bin/uv.exe",
        "run",
        "--directory",
        "C:\\repo\\packages\\agent",
        "inari",
    )


def test_build_control_bridge_uses_monitor_fallback() -> None:
    bridge = build_control_bridge(TraySettings(control_mode="monitor"))

    state = bridge.query_state()

    assert state.mode is ControlMode.MONITOR
    assert state.lifecycle is LifecycleState.UNKNOWN


def test_build_control_bridge_selects_systemd_service_on_linux() -> None:
    bridge = build_control_bridge(
        TraySettings(control_mode="service", service_name="inari.service"),
        platform_name="Linux",
    )

    assert isinstance(bridge, SystemdAgentBridge)


def test_build_control_bridge_selects_launchd_service_on_macos() -> None:
    bridge = build_control_bridge(
        TraySettings(control_mode="service", service_name="com.example.inari"),
        platform_name="Darwin",
    )

    assert isinstance(bridge, LaunchdAgentBridge)


def test_build_control_bridge_reports_unsupported_service_platform() -> None:
    bridge = build_control_bridge(
        TraySettings(control_mode="service"),
        platform_name="FreeBSD",
    )

    assert isinstance(bridge, UnsupportedServiceAgentBridge)
    assert bridge.query_state().lifecycle is LifecycleState.UNKNOWN


def test_query_state_parses_active_system_service() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            list(command), 0, stdout="active\n", stderr=""
        )

    bridge = SystemdAgentBridge(
        TraySettings(
            control_mode="service",
            service_name="inari.service",
            service_scope="system",
        ),
        runner=runner,
    )

    state = bridge.query_state()

    assert commands == [
        ("systemctl", "show", "inari.service", "--property=ActiveState", "--value")
    ]
    assert state.lifecycle is LifecycleState.RUNNING
    assert "systemd service" in (state.detail or "")


def test_query_state_uses_user_systemd_scope() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            list(command), 0, stdout="inactive\n", stderr=""
        )

    bridge = SystemdAgentBridge(
        TraySettings(
            control_mode="service",
            service_name="inari.service",
            service_scope="user",
        ),
        runner=runner,
    )

    state = bridge.query_state()

    assert commands == [
        (
            "systemctl",
            "--user",
            "show",
            "inari.service",
            "--property=ActiveState",
            "--value",
        )
    ]
    assert state.lifecycle is LifecycleState.STOPPED
    assert "user" in (state.detail or "")


def test_query_state_parses_running_launchd_job() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            list(command), 0, stdout="state = running\n", stderr=""
        )

    bridge = LaunchdAgentBridge(
        TraySettings(
            control_mode="service",
            service_name="com.example.inari",
            service_scope="user",
        ),
        runner=runner,
    )

    state = bridge.query_state()

    assert commands == [("launchctl", "print", "gui/0/com.example.inari")]
    assert state.lifecycle is LifecycleState.RUNNING
    assert "launchd job" in (state.detail or "")


def test_query_state_treats_missing_launchd_job_as_stopped() -> None:
    def runner(command):
        raise RuntimeError('Could not find service "gui/0/com.example.inari"')

    bridge = LaunchdAgentBridge(
        TraySettings(
            control_mode="service",
            service_name="com.example.inari",
            service_scope="user",
        ),
        runner=runner,
    )

    state = bridge.query_state()

    assert state.lifecycle is LifecycleState.STOPPED
    assert "launchd job" in (state.detail or "")


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
        self.activate_callback = None

    def run(self, *, snapshot, menu_entries, on_ready, on_activate=None) -> None:
        self.run_called = True
        self.initial_snapshot = snapshot
        self.initial_menu_entries = list(menu_entries)
        self.activate_callback = on_activate
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


class FakeDeviceCenter:
    def __init__(self) -> None:
        self.show_calls = 0
        self.closed = False
        self.snapshots: list[TraySnapshot] = []
        self.events: list[RuntimeEventResponse] = []

    def show(self) -> None:
        self.show_calls += 1

    def update_connection_snapshot(self, snapshot: TraySnapshot) -> None:
        self.snapshots.append(snapshot)

    def handle_runtime_event(self, event: RuntimeEventResponse) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.closed = True


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

    def mark_ready(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


class FakeServiceControlBridge:
    mode = ControlMode.SERVICE

    def __init__(self, *, lifecycle: LifecycleState = LifecycleState.RUNNING) -> None:
        self.lifecycle = lifecycle

    def query_state(self):
        return ControlSnapshot(
            mode=ControlMode.SERVICE,
            lifecycle=self.lifecycle,
            detail="Managing platform service 'inari'.",
            can_start=self.lifecycle
            in {LifecycleState.STOPPED, LifecycleState.UNKNOWN},
            can_stop=self.lifecycle
            in {LifecycleState.RUNNING, LifecycleState.STARTING},
            can_restart=self.lifecycle
            in {
                LifecycleState.RUNNING,
                LifecycleState.STARTING,
                LifecycleState.STOPPED,
            },
        )

    def mark_ready(self) -> None:
        return None


class FakeTrayClient:
    def get_status(self) -> SystemStatusResponse:
        return SystemStatusResponse.model_validate(
            {
                "ok": True,
                "status": "healthy",
                "service": {"name": "Inari", "version": API_VERSION},
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


class FakeQtTrayIcon:
    def __init__(self) -> None:
        self.icon = None
        self.tooltip = None

    def setIcon(self, icon) -> None:
        self.icon = icon

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setContextMenu(self, menu) -> None:
        return None

    def hide(self) -> None:
        return None


class FakeQtMenu:
    def __init__(self, *, visible: bool) -> None:
        self._visible = visible

    def isVisible(self) -> bool:
        return self._visible

    def clear(self) -> None:
        return None

    def addSeparator(self) -> None:
        return None

    def addAction(self, action) -> None:
        return None

    def setDefaultAction(self, action) -> None:
        return None


def bridge_module_sys_executable() -> str:
    import sys

    return sys.executable


def _tray_snapshot() -> TraySnapshot:
    return TraySnapshot.initial(
        title="Inari",
        links=TrayLinks(
            api_base_url="http://127.0.0.1:7310",
            docs_url="http://127.0.0.1:7310/docs",
            devices_url="http://127.0.0.1:7310/devices",
            jobs_url="http://127.0.0.1:7310/jobs",
            log_dir=Path("./logs"),
        ),
        control=ControlSnapshot(
            mode=ControlMode.MONITOR, lifecycle=LifecycleState.RUNNING
        ),
    )
