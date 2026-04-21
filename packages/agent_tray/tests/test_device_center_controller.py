from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtWidgets import QApplication

from inari.local_api.schemas import (
    DeviceDirectoryResponse,
    DeviceEventCollectionResponse,
    DeviceResponse,
    JobResourceResponse,
    RuntimeEventResponse,
)
from inari_tray.config import TraySettings
from inari_tray.device_center.controller import (
    QtDeviceCenterController,
    _event_requires_directory_refresh,
)
from inari_tray.device_center.helpers import DEFAULT_EVENT_LIMIT
from inari_tray.device_center.window import DeviceCenterWindow


def test_event_requires_directory_refresh_only_for_device_directory_events() -> None:
    assert _event_requires_directory_refresh(
        _runtime_event("device.connected", resource_kind="device")
    )
    assert _event_requires_directory_refresh(
        _runtime_event("device.updated", resource_kind="device")
    )
    assert _event_requires_directory_refresh(
        _runtime_event("device.disconnected", resource_kind="device")
    )
    assert not _event_requires_directory_refresh(
        _runtime_event("job.queued", resource_kind="job", resource_id="job_123")
    )


def test_device_center_reuses_cached_device_events_until_marked_stale(
    monkeypatch,
) -> None:
    _qt_app()
    monkeypatch.setattr(
        "inari_tray.device_center.controller.threading.Thread",
        _ImmediateThread,
    )
    client = _FakeClient()
    controller = QtDeviceCenterController(
        settings=TraySettings(device_center_refresh_interval_seconds=1.0),
        client=client,
    )
    controller._refresh_timer.stop()
    controller._coalesced_refresh_timer.stop()

    controller._events_by_device_id["dev_printer"] = (
        _runtime_event("device.connected"),
    )

    controller._load_device_events("dev_printer")
    assert client.device_event_requests == []

    controller._stale_device_event_ids.add("dev_printer")
    controller._load_device_events("dev_printer")

    assert client.device_event_requests == [("dev_printer", DEFAULT_EVENT_LIMIT)]
    assert "dev_printer" not in controller._stale_device_event_ids
    controller.close()


def test_device_table_tab_moves_to_next_row() -> None:
    _qt_app()
    window = DeviceCenterWindow(title="Inari")
    window.set_devices(
        [_device_response("dev_1", "Alpha"), _device_response("dev_2", "Beta")],
        selected_device_id="dev_1",
        pinned_device_ids=set(),
    )
    assert window._device_table.focusNextPrevChild(True)

    assert window._selected_device_id() == "dev_2"
    window.close()


class _ImmediateThread:
    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _FakeClient:
    def __init__(self) -> None:
        self.device_event_requests: list[tuple[str, int]] = []

    def list_devices(self) -> DeviceDirectoryResponse:
        return DeviceDirectoryResponse.model_validate(
            {
                "devices": [],
                "summary": {
                    "count": 0,
                    "online_count": 0,
                    "offline_count": 0,
                    "kind_counts": {},
                    "default_device": None,
                },
            }
        )

    def list_device_events(
        self, device_id: str, *, limit: int = DEFAULT_EVENT_LIMIT
    ) -> DeviceEventCollectionResponse:
        self.device_event_requests.append((device_id, limit))
        return DeviceEventCollectionResponse.model_validate(
            {
                "ok": True,
                "events": [
                    _runtime_event("device.updated", resource_id=device_id).model_dump(
                        mode="json"
                    )
                ],
            }
        )

    def submit_test_page(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> JobResourceResponse:
        del device_id, printer_name
        return JobResourceResponse.model_validate(
            {
                "ok": True,
                "job": {
                    "id": "job_test_page",
                    "kind": "device_command",
                    "operation": "print_test_page",
                    "state": "queued",
                    "target": {
                        "device_id": "dev_printer",
                        "device_kind": "printer",
                        "device_name": "Kitchen Printer",
                    },
                    "command_kind": "print_test_page",
                    "attempt_count": 0,
                    "max_attempts": 3,
                    "created_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
                    "updated_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
                    "queued_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
                    "next_run_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
                    "metadata": {},
                },
            }
        )

    def open_cash_drawer(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> JobResourceResponse:
        return self.submit_test_page(
            device_id=device_id,
            printer_name=printer_name,
        )


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _runtime_event(
    event_type: str,
    *,
    resource_kind: str = "device",
    resource_id: str = "dev_printer",
) -> RuntimeEventResponse:
    return RuntimeEventResponse.model_validate(
        {
            "sequence": 7,
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            "event_type": event_type,
            "occurred_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
            "payload": {"name": "Kitchen Printer"},
        }
    )


def _device_response(device_id: str, name: str) -> DeviceResponse:
    return DeviceResponse.model_validate(
        {
            "id": device_id,
            "kind": "printer",
            "device_class": "physical",
            "name": name,
            "driver_key": "windows_spooler",
            "driver": {
                "key": "windows_spooler",
                "display_name": "Windows Print Spooler",
                "kind": "printer",
                "platform": "windows",
            },
            "connection": {
                "state": "online",
                "first_seen_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
                "observed_at": datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
            },
            "printer": {
                "is_default": False,
                "preferred_transport": "document",
                "supported_transports": ["document"],
                "capabilities": [],
            },
            "metadata": {},
        }
    )
