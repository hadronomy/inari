from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser
from typing import Any, Callable

from iot_agent.models import LiveEventUpdateResponse, LiveSnapshotResponse, RuntimeEventResponse, SystemStatusResponse

from .bridge import AgentControlBridge, build_control_bridge
from .client import AgentApiClient
from .config import TraySettings
from .icons import build_tray_icon
from .models import ControlMode, ControlSnapshot, TrayLinks, TraySnapshot

logger = logging.getLogger(__name__)


class AgentTrayApplication:
    def __init__(
        self,
        settings: TraySettings,
        *,
        client: AgentApiClient | None = None,
        bridge: AgentControlBridge | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or AgentApiClient(settings)
        self.bridge = bridge or build_control_bridge(settings)
        self.links = TrayLinks(
            api_base_url=settings.agent_api_base_url,
            docs_url=settings.agent_docs_url,
            devices_url=settings.agent_devices_url,
            jobs_url=settings.agent_jobs_url,
            log_dir=settings.log_dir,
        )
        self._snapshot_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._icon: Any | None = None
        self._pystray: Any | None = None
        self._threads: list[threading.Thread] = []
        self._snapshot = TraySnapshot.initial(
            title=settings.title,
            links=self.links,
            control=self.bridge.query_state(),
        )

    @classmethod
    def from_settings(cls, settings: TraySettings) -> AgentTrayApplication:
        return cls(settings)

    @property
    def snapshot(self) -> TraySnapshot:
        with self._snapshot_lock:
            return self._snapshot

    def run(self) -> None:
        import pystray

        self._pystray = pystray
        snapshot = self.snapshot
        logger.info("Starting tray icon for %s", self.settings.title)
        icon = pystray.Icon(
            "iot-agent-tray",
            icon=build_tray_icon(snapshot),
            title=snapshot.tooltip,
            menu=self._build_menu(pystray),
        )
        self._icon = icon
        icon.run(setup=self._setup_background)

    def _setup_background(self, icon: Any) -> None:
        self._icon = icon
        self._icon.visible = True
        logger.info("Tray icon is now visible")
        self._ensure_local_agent_started()
        self._refresh_snapshot(notify_connection=False)
        self._threads = [
            threading.Thread(target=self._reconcile_loop, name="iot-agent-tray-reconcile", daemon=True),
            threading.Thread(target=self._event_loop, name="iot-agent-tray-events", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        logger.info("Started %d tray background threads", len(self._threads))

    def _ensure_local_agent_started(self) -> None:
        if self.settings.control_mode != "spawn" or not self.settings.auto_start_agent:
            return
        try:
            self.client.get_status()
        except Exception:
            pass
        else:
            logger.info("Agent API is already reachable; skipping auto-start")
            return
        control = self.bridge.query_state()
        if not control.can_start:
            return
        try:
            message = self.bridge.start()
        except Exception:
            logger.exception("Failed to auto-start the local agent process")
            return
        logger.info("%s", message)

    def _reconcile_loop(self) -> None:
        while not self._stop_event.wait(self.settings.status_reconcile_interval_seconds):
            self._refresh_snapshot()

    def _event_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                for message in self.client.iter_live_updates(self._stop_event):
                    control = self.bridge.query_state()
                    if isinstance(message, LiveSnapshotResponse):
                        self._apply_status_snapshot(
                            message.status,
                            control=control,
                            notify_connection=True,
                        )
                        continue
                    if isinstance(message, LiveEventUpdateResponse):
                        self._apply_status_snapshot(
                            message.status,
                            control=control,
                            event=message.event,
                            notify_connection=True,
                        )
                        self._notify_for_event(message.event)
                        if self._stop_event.is_set():
                            return
                        continue
                    if self._stop_event.is_set():
                        return
            except Exception as exc:
                logger.debug("Tray event stream disconnected: %s", exc)
                self._refresh_snapshot(notify_connection=False)
                if self._stop_event.wait(self.settings.event_reconnect_delay_seconds):
                    return

    def _refresh_snapshot(self, *, notify_connection: bool = True) -> None:
        with self._refresh_lock:
            control = self.bridge.query_state()
            try:
                status = self.client.get_status()
            except Exception as exc:
                previous = self.snapshot
                snapshot = previous.with_error(
                    control=control,
                    message=_connection_failure_message(control, exc),
                )
                self._apply_snapshot(snapshot)
                if notify_connection and previous.connected != snapshot.connected:
                    self._notify_connection_change(snapshot)
            else:
                self._apply_status_snapshot(
                    status,
                    control=control,
                    notify_connection=notify_connection,
                )

    def _apply_status_snapshot(
        self,
        status: SystemStatusResponse,
        *,
        control: ControlSnapshot,
        event: RuntimeEventResponse | None = None,
        notify_connection: bool,
    ) -> None:
        previous = self.snapshot
        snapshot = TraySnapshot.from_status(
            title=self.settings.title,
            links=self.links,
            control=control,
            status=status,
            previous=previous,
        )
        if event is not None:
            snapshot = snapshot.with_event(event)
        self._apply_snapshot(snapshot)
        if notify_connection and previous.connected != snapshot.connected:
            self._notify_connection_change(snapshot)

    def _apply_snapshot(self, snapshot: TraySnapshot) -> None:
        with self._snapshot_lock:
            self._snapshot = snapshot
        if self._icon is None or self._pystray is None:
            return
        try:
            self._icon.icon = build_tray_icon(snapshot)
            self._icon.title = snapshot.tooltip
            self._icon.menu = self._build_menu(self._pystray)
            self._icon.update_menu()
        except Exception:
            logger.exception("Failed to apply tray snapshot")

    def _build_menu(self, pystray_module: Any) -> Any:
        Menu = pystray_module.Menu
        MenuItem = pystray_module.MenuItem
        snapshot = self.snapshot
        items = [
            MenuItem(snapshot.status_line, None, enabled=False),
            MenuItem(snapshot.control_line, None, enabled=False),
            MenuItem(snapshot.device_line, None, enabled=False),
            MenuItem(snapshot.queue_line, None, enabled=False),
        ]
        if snapshot.error_line:
            items.append(MenuItem(snapshot.error_line, None, enabled=False))
        items.extend(
            [
                Menu.SEPARATOR,
                MenuItem("Open API Docs", self._open_docs, default=True),
                MenuItem("Open Devices", self._open_devices),
                MenuItem("Open Queue", self._open_jobs),
                MenuItem("Open Logs", self._open_logs),
                Menu.SEPARATOR,
                MenuItem("Print Test Page", self._print_test_page, enabled=snapshot.connected),
                MenuItem("Refresh Now", self._refresh_now),
                MenuItem(self._start_label(snapshot), self._start_agent, enabled=self._can_start(snapshot)),
                MenuItem(self._stop_label(snapshot), self._stop_agent, enabled=self._can_stop(snapshot)),
                MenuItem(self._restart_label(snapshot), self._restart_agent, enabled=self._can_restart(snapshot)),
                Menu.SEPARATOR,
                MenuItem("Quit Tray", self._quit_tray),
            ]
        )
        return Menu(*items)

    def _open_docs(self, *_: object) -> None:
        webbrowser.open(self.links.docs_url)

    def _open_devices(self, *_: object) -> None:
        webbrowser.open(self.links.devices_url)

    def _open_jobs(self, *_: object) -> None:
        webbrowser.open(self.links.jobs_url)

    def _open_logs(self, *_: object) -> None:
        self.links.log_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(self.links.log_dir)  # type: ignore[attr-defined]
            return
        webbrowser.open(self.links.log_dir.as_uri())

    def _refresh_now(self, *_: object) -> None:
        self._launch_background(self._refresh_snapshot, name="iot-agent-tray-refresh")

    def _print_test_page(self, *_: object) -> None:
        self._launch_background(self._print_test_page_sync, name="iot-agent-tray-test-page")

    def _start_agent(self, *_: object) -> None:
        self._launch_background(
            lambda: self._run_control_action(self.bridge.start, expect_connected=True),
            name="iot-agent-tray-start",
        )

    def _stop_agent(self, *_: object) -> None:
        self._launch_background(
            lambda: self._run_control_action(self.bridge.stop, expect_connected=False),
            name="iot-agent-tray-stop",
        )

    def _restart_agent(self, *_: object) -> None:
        self._launch_background(
            lambda: self._run_control_action(self.bridge.restart, expect_connected=True),
            name="iot-agent-tray-restart",
        )

    def _quit_tray(self, *_: object) -> None:
        self._stop_event.set()
        try:
            self.bridge.shutdown()
        finally:
            if self._icon is not None:
                self._icon.stop()

    def _print_test_page_sync(self) -> None:
        try:
            job = self.client.submit_test_page()
        except Exception as exc:
            self._notify("Unable to queue a test page.", subtitle=_humanize_exception(exc))
            return
        self._notify("Queued a printer test page.", subtitle=f"Job {job.job.id} is waiting in the queue.")
        self._refresh_snapshot(notify_connection=False)

    def _run_control_action(
        self,
        operation: Callable[[], str],
        *,
        expect_connected: bool | None,
    ) -> None:
        try:
            message = operation()
        except Exception as exc:
            self._notify("Control action failed.", subtitle=_humanize_exception(exc))
            self._refresh_snapshot(notify_connection=False)
            return
        self._notify(message)
        if expect_connected is not None:
            self._wait_for_connection(expect_connected, timeout_seconds=15.0)
        self._refresh_snapshot(notify_connection=False)

    def _wait_for_connection(self, expected: bool, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not self._stop_event.is_set():
            self._refresh_snapshot(notify_connection=False)
            if self.snapshot.connected is expected:
                return
            time.sleep(0.5)

    def _notify_connection_change(self, snapshot: TraySnapshot) -> None:
        if snapshot.connected:
            self._notify("Connected to the local agent.", subtitle=snapshot.queue_line)
            return
        self._notify("Lost connection to the local agent.", subtitle=snapshot.control_line)

    def _notify_for_event(self, event: RuntimeEventResponse) -> None:
        if event.event_type == "job.failed":
            self._notify("A queued job failed.", subtitle=_event_subtitle(event))
            return
        if event.event_type == "device.disconnected":
            self._notify("A device disconnected.", subtitle=_event_subtitle(event))
            return
        if event.event_type == "device.connected":
            self._notify("A device connected.", subtitle=_event_subtitle(event))

    def _notify(self, message: str, *, subtitle: str | None = None) -> None:
        if self._icon is None:
            return
        if not getattr(self._icon, "HAS_NOTIFICATION", False):
            return
        body = message if subtitle is None else f"{message}\n{subtitle}"
        try:
            self._icon.notify(body, self.settings.title)
        except Exception:
            logger.debug("Tray notification failed", exc_info=True)

    def _launch_background(self, target: Callable[[], None], *, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()

    @staticmethod
    def _can_start(snapshot: TraySnapshot) -> bool:
        return snapshot.control.can_start and not snapshot.connected

    @staticmethod
    def _can_stop(snapshot: TraySnapshot) -> bool:
        return snapshot.control.can_stop

    @staticmethod
    def _can_restart(snapshot: TraySnapshot) -> bool:
        return snapshot.control.can_restart

    @staticmethod
    def _start_label(snapshot: TraySnapshot) -> str:
        return "Start Service" if snapshot.control.mode is ControlMode.SERVICE else "Start Agent"

    @staticmethod
    def _stop_label(snapshot: TraySnapshot) -> str:
        return "Stop Service" if snapshot.control.mode is ControlMode.SERVICE else "Stop Agent"

    @staticmethod
    def _restart_label(snapshot: TraySnapshot) -> str:
        return "Restart Service" if snapshot.control.mode is ControlMode.SERVICE else "Restart Agent"


def _event_subtitle(event: RuntimeEventResponse) -> str:
    detail = event.payload.get("error_detail") or event.payload.get("name") or event.payload.get("device_name")
    if isinstance(detail, str) and detail:
        return detail
    return event.event_type.replace(".", " ")


def _humanize_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return type(exc).__name__


def _connection_failure_message(control, exc: Exception) -> str:
    if control.mode is ControlMode.SPAWN and control.lifecycle is not None and control.detail:
        if control.lifecycle is not None and "exited with code" in control.detail:
            return control.detail
    return _humanize_exception(exc)
