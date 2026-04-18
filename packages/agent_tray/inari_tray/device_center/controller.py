from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QSettings, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from inari.models import DeviceDirectoryResponse, DeviceResponse, RuntimeEventResponse

from ..client import AgentApiClient
from ..config import TraySettings
from ..models import TraySnapshot
from .helpers import (
    DEFAULT_EVENT_LIMIT,
    SETTINGS_APPLICATION,
    SETTINGS_GROUP,
    SETTINGS_ORGANIZATION,
    humanize_exception,
)
from .window import DeviceCenterWindow


class _ControllerSignals(QObject):
    show_requested = Signal()
    close_requested = Signal()
    snapshot_updated = Signal(object)
    runtime_event_received = Signal(object)
    devices_loaded = Signal(int, object)
    device_events_loaded = Signal(int, str, object)
    load_failed = Signal(str, str)
    action_succeeded = Signal(str, str)
    action_failed = Signal(str, str)


class QtDeviceCenterController(QObject):
    def __init__(
        self,
        *,
        settings: TraySettings,
        client: AgentApiClient,
        notify: Callable[[str, str | None], None] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.client = client
        self._notify = notify
        self._window = DeviceCenterWindow(title=settings.title)
        self._signals = _ControllerSignals()
        self._qt_settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        self._devices_by_id: dict[str, DeviceResponse] = {}
        self._events_by_device_id: dict[str, tuple[RuntimeEventResponse, ...]] = {}
        self._selected_device_id = self._load_selected_device_id()
        self._pinned_device_ids = self._load_pinned_device_ids()
        self._devices_loaded_once = False
        self._refresh_generation = 0
        self._event_generation = 0

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(
            max(1000, int(settings.device_center_refresh_interval_seconds * 1000))
        )
        self._refresh_timer.timeout.connect(self._refresh_devices)
        self._refresh_timer.start()

        self._coalesced_refresh_timer = QTimer(self)
        self._coalesced_refresh_timer.setInterval(400)
        self._coalesced_refresh_timer.setSingleShot(True)
        self._coalesced_refresh_timer.timeout.connect(self._refresh_devices)

        self._window.restore_geometry_state(
            self._qt_settings.value(self._settings_key("geometry"))
        )
        self._window.set_filter_state(
            online_only=self._load_bool_setting("online_only"),
            pinned_only=self._load_bool_setting("pinned_only"),
        )
        self._window.set_pinned_device_ids(self._pinned_device_ids)

        self._window.refresh_requested.connect(
            lambda: self._refresh_devices(force=True)
        )
        self._window.selection_changed.connect(self._on_selection_changed)
        self._window.print_test_page_requested.connect(
            self._on_print_test_page_requested
        )
        self._window.open_cash_drawer_requested.connect(
            self._on_open_cash_drawer_requested
        )
        self._window.copy_device_info_requested.connect(self._copy_device_info)
        self._window.pin_requested.connect(self._on_pin_requested)
        self._window.online_only_changed.connect(
            lambda value: self._persist_bool_setting("online_only", value)
        )
        self._window.pinned_only_changed.connect(
            lambda value: self._persist_bool_setting("pinned_only", value)
        )
        self._window.geometry_persist_requested.connect(self._persist_geometry)

        self._signals.show_requested.connect(self._show_window)
        self._signals.close_requested.connect(self._close_window)
        self._signals.snapshot_updated.connect(self._apply_snapshot)
        self._signals.runtime_event_received.connect(self._apply_runtime_event)
        self._signals.devices_loaded.connect(self._on_devices_loaded)
        self._signals.device_events_loaded.connect(self._on_device_events_loaded)
        self._signals.load_failed.connect(self._on_background_load_failed)
        self._signals.action_succeeded.connect(self._on_action_succeeded)
        self._signals.action_failed.connect(self._on_action_failed)

    def show(self) -> None:
        self._signals.show_requested.emit()

    def update_connection_snapshot(self, snapshot: TraySnapshot) -> None:
        self._signals.snapshot_updated.emit(snapshot)

    def handle_runtime_event(self, event: RuntimeEventResponse) -> None:
        self._signals.runtime_event_received.emit(event)

    def close(self) -> None:
        self._signals.close_requested.emit()

    def _show_window(self) -> None:
        self._window.show_window()
        if not self._devices_loaded_once:
            self._refresh_devices(force=True)

    def _close_window(self) -> None:
        self._persist_geometry(self._window.saveGeometry())
        self._refresh_timer.stop()
        self._coalesced_refresh_timer.stop()
        self._window.begin_shutdown()
        self._window.close()

    def _apply_snapshot(self, snapshot: TraySnapshot) -> None:
        self._window.set_connection_state(snapshot)

    def _apply_runtime_event(self, event: RuntimeEventResponse) -> None:
        if (
            self._selected_device_id is not None
            and event.resource_kind == "device"
            and event.resource_id == self._selected_device_id
        ):
            current_events = self._events_by_device_id.get(
                self._selected_device_id,
                (),
            )
            deduped = [
                existing
                for existing in current_events
                if existing.sequence != event.sequence
            ]
            updated = tuple([event, *deduped][:DEFAULT_EVENT_LIMIT])
            self._events_by_device_id[self._selected_device_id] = updated
            device = self._devices_by_id.get(self._selected_device_id)
            if device is not None:
                self._window.set_device_details(
                    device,
                    updated,
                    pinned=device.id in self._pinned_device_ids,
                )
        self._coalesced_refresh_timer.start()

    def _refresh_devices(self, *, force: bool = False) -> None:
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._window.set_busy_message(
            "Refreshing devices…" if force else "Updating device data…"
        )

        def worker() -> None:
            try:
                directory = self.client.list_devices()
            except Exception as exc:  # pragma: no cover
                self._signals.load_failed.emit("devices", humanize_exception(exc))
                return
            self._signals.devices_loaded.emit(generation, directory)

        threading.Thread(
            target=worker,
            name="inari-tray-device-center-refresh",
            daemon=True,
        ).start()

    def _load_device_events(self, device_id: str) -> None:
        self._event_generation += 1
        generation = self._event_generation
        self._window.set_busy_message("Loading recent device events…")

        def worker() -> None:
            try:
                events = self.client.list_device_events(
                    device_id,
                    limit=DEFAULT_EVENT_LIMIT,
                )
            except Exception as exc:  # pragma: no cover
                self._signals.load_failed.emit("events", humanize_exception(exc))
                return
            self._signals.device_events_loaded.emit(generation, device_id, events)

        threading.Thread(
            target=worker,
            name="inari-tray-device-center-events",
            daemon=True,
        ).start()

    def _on_devices_loaded(
        self,
        generation: int,
        directory: DeviceDirectoryResponse,
    ) -> None:
        if generation != self._refresh_generation:
            return
        self._devices_loaded_once = True
        self._devices_by_id = {device.id: device for device in directory.devices}
        selected_id = self._window.set_devices(
            directory.devices,
            selected_device_id=self._selected_device_id,
            pinned_device_ids=self._pinned_device_ids,
        )
        self._selected_device_id = selected_id
        self._persist_selected_device_id(selected_id)
        device = (
            self._devices_by_id.get(selected_id) if selected_id is not None else None
        )
        cached_events = (
            self._events_by_device_id.get(selected_id, ())
            if selected_id is not None
            else ()
        )
        self._window.set_device_details(
            device,
            cached_events,
            pinned=bool(device is not None and device.id in self._pinned_device_ids),
        )
        self._window.set_busy_message(None)
        if selected_id is not None:
            self._load_device_events(selected_id)

    def _on_device_events_loaded(
        self,
        generation: int,
        device_id: str,
        response: object,
    ) -> None:
        if generation != self._event_generation or not hasattr(response, "events"):
            return
        events = tuple(response.events)
        self._events_by_device_id[device_id] = events
        if device_id != self._selected_device_id:
            return
        device = self._devices_by_id.get(device_id)
        self._window.set_device_details(
            device,
            events,
            pinned=bool(device is not None and device.id in self._pinned_device_ids),
        )
        self._window.set_busy_message(None)

    def _on_background_load_failed(self, kind: str, detail: str) -> None:
        self._window.set_busy_message(None)
        if kind == "devices":
            self._window.show_status_note(
                f"Unable to refresh devices: {detail}",
                mode="offline",
                timeout_ms=8000,
            )
            return
        self._window.show_status_note(
            f"Unable to load device events: {detail}",
            mode="offline",
            timeout_ms=8000,
        )

    def _on_selection_changed(self, device: DeviceResponse | None) -> None:
        self._selected_device_id = device.id if device is not None else None
        self._persist_selected_device_id(self._selected_device_id)
        events = (
            self._events_by_device_id.get(device.id, ()) if device is not None else ()
        )
        self._window.set_device_details(
            device,
            events,
            pinned=bool(device is not None and device.id in self._pinned_device_ids),
        )
        if device is not None:
            self._load_device_events(device.id)

    def _on_pin_requested(self, device: DeviceResponse | None, pinned: bool) -> None:
        if device is None:
            return
        if pinned:
            self._pinned_device_ids.add(device.id)
        else:
            self._pinned_device_ids.discard(device.id)
        self._persist_pinned_device_ids()
        self._window.set_pinned_device_ids(self._pinned_device_ids)
        self._window.set_device_details(
            device,
            self._events_by_device_id.get(device.id, ()),
            pinned=device.id in self._pinned_device_ids,
        )

    def _on_print_test_page_requested(self, device: DeviceResponse | None) -> None:
        if device is None:
            return
        self._run_device_action(
            device,
            label="Queueing a test page…",
            success_title="Queued a printer test page.",
            operation=lambda: self.client.submit_test_page(device_id=device.id),
        )

    def _on_open_cash_drawer_requested(self, device: DeviceResponse | None) -> None:
        if device is None:
            return
        self._run_device_action(
            device,
            label="Opening the cash drawer…",
            success_title="Queued the cash drawer action.",
            operation=lambda: self.client.open_cash_drawer(device_id=device.id),
        )

    def _run_device_action(
        self,
        device: DeviceResponse,
        *,
        label: str,
        success_title: str,
        operation: Callable[[], Any],
    ) -> None:
        self._window.set_busy_message(label)

        def worker() -> None:
            try:
                job = operation()
            except Exception as exc:  # pragma: no cover
                self._signals.action_failed.emit(
                    "Control action failed.",
                    humanize_exception(exc),
                )
                return
            subtitle = f"Job {job.job.id} is waiting in the queue."
            self._signals.action_succeeded.emit(success_title, subtitle)

        threading.Thread(
            target=worker,
            name=f"inari-tray-device-action-{device.id}",
            daemon=True,
        ).start()

    def _on_action_succeeded(self, title: str, subtitle: str) -> None:
        self._window.set_busy_message(None)
        self._window.show_status_note(title, timeout_ms=5000)
        self._maybe_notify(title, subtitle)
        self._refresh_devices(force=True)

    def _on_action_failed(self, title: str, subtitle: str) -> None:
        self._window.set_busy_message(None)
        self._window.show_status_note(
            subtitle,
            mode="offline",
            timeout_ms=8000,
        )
        self._maybe_notify(title, subtitle)

    def _copy_device_info(self, device: DeviceResponse | None = None) -> None:
        if device is None:
            device = self._devices_by_id.get(self._selected_device_id or "")
        if device is None:
            return
        payload = {
            "device": device.model_dump(mode="json"),
            "events": [
                event.model_dump(mode="json")
                for event in self._events_by_device_id.get(device.id, ())
            ],
        }
        QGuiApplication.clipboard().setText(
            json.dumps(payload, indent=2, sort_keys=True)
        )
        self._window.show_status_note(
            "Copied device information to the clipboard.",
            timeout_ms=4000,
        )
        self._maybe_notify(
            "Copied device information.",
            "The current device details are now on the clipboard.",
        )

    def _persist_geometry(self, geometry: object) -> None:
        self._qt_settings.setValue(self._settings_key("geometry"), geometry)

    def _persist_selected_device_id(self, device_id: str | None) -> None:
        self._qt_settings.setValue(
            self._settings_key("selected_device_id"),
            device_id or "",
        )

    def _persist_pinned_device_ids(self) -> None:
        self._qt_settings.setValue(
            self._settings_key("pinned_device_ids"),
            json.dumps(sorted(self._pinned_device_ids)),
        )

    def _persist_bool_setting(self, key: str, value: bool) -> None:
        self._qt_settings.setValue(self._settings_key(key), bool(value))

    def _load_selected_device_id(self) -> str | None:
        value = self._qt_settings.value(self._settings_key("selected_device_id"), "")
        if isinstance(value, str) and value:
            return value
        return None

    def _load_pinned_device_ids(self) -> set[str]:
        raw = self._qt_settings.value(self._settings_key("pinned_device_ids"), "[]")
        if not isinstance(raw, str):
            return set()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return set()
        return {item for item in parsed if isinstance(item, str) and item}

    def _load_bool_setting(self, key: str) -> bool:
        value = self._qt_settings.value(self._settings_key(key), False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return bool(value)

    def _settings_key(self, key: str) -> str:
        return f"{SETTINGS_GROUP}/{key}"

    def _maybe_notify(self, title: str, subtitle: str | None) -> None:
        if self._notify is not None:
            self._notify(title, subtitle)
