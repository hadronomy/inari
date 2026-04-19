from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtWidgets import QApplication

from inari.models import DeviceEventCollectionResponse, RuntimeEventResponse
from inari_tray.config import TraySettings
from inari_tray.device_center.controller import (
    QtDeviceCenterController,
    _event_requires_directory_refresh,
)
from inari_tray.device_center.helpers import DEFAULT_EVENT_LIMIT


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

    controller._events_by_device_id["dev_printer"] = (_runtime_event("device.connected"),)

    controller._load_device_events("dev_printer")
    assert client.device_event_requests == []

    controller._stale_device_event_ids.add("dev_printer")
    controller._load_device_events("dev_printer")

    assert client.device_event_requests == [("dev_printer", DEFAULT_EVENT_LIMIT)]
    assert "dev_printer" not in controller._stale_device_event_ids
    controller.close()


class _ImmediateThread:
    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _FakeClient:
    def __init__(self) -> None:
        self.device_event_requests: list[tuple[str, int]] = []

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


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is not None:
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
