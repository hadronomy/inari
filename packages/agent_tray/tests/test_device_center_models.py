from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QCoreApplication, Qt

from inari.local_api.schemas import DeviceResponse
from inari_tray.device_center.table_models import DeviceTableModel


def test_device_table_model_refresh_does_not_reset_the_model() -> None:
    _qt_app()
    model = DeviceTableModel()
    reset_calls: list[bool] = []
    model.modelReset.connect(lambda: reset_calls.append(True))

    model.set_devices(
        [_device("dev_kitchen", "Kitchen Printer")], pinned_device_ids=set()
    )
    model.set_devices(
        [_device("dev_kitchen", "Kitchen Printer v2")],
        pinned_device_ids={"dev_kitchen"},
    )

    assert reset_calls == []
    assert model.rowCount() == 1
    assert (
        model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole)
        == "Kitchen Printer v2"
    )
    assert "Pinned" in model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)


def test_device_table_model_uses_row_changes_for_directory_updates() -> None:
    _qt_app()
    model = DeviceTableModel()
    reset_calls: list[bool] = []
    inserted: list[tuple[int, int]] = []
    removed: list[tuple[int, int]] = []
    model.modelReset.connect(lambda: reset_calls.append(True))
    model.rowsInserted.connect(
        lambda _parent, first, last: inserted.append((first, last))
    )
    model.rowsRemoved.connect(
        lambda _parent, first, last: removed.append((first, last))
    )

    model.set_devices(
        [
            _device("dev_front", "Front Counter"),
            _device("dev_kitchen", "Kitchen Printer"),
        ],
        pinned_device_ids=set(),
    )
    inserted.clear()
    removed.clear()

    model.set_devices(
        [
            _device("dev_kitchen", "Kitchen Printer"),
            _device("dev_bar", "Bar Printer"),
        ],
        pinned_device_ids=set(),
    )

    assert reset_calls == []
    assert removed == [(0, 0)]
    assert inserted == [(1, 1)]
    device_zero = model.device_at(0)
    assert device_zero is not None
    assert device_zero.id == "dev_kitchen"
    device_one = model.device_at(1)
    assert device_one is not None
    assert device_one.id == "dev_bar"


def _qt_app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is not None:
        return app
    return QCoreApplication([])


def _device(device_id: str, name: str) -> DeviceResponse:
    return DeviceResponse.model_validate(
        {
            "id": device_id,
            "kind": "printer",
            "device_class": "physical",
            "name": name,
            "driver_key": "tests.fake-printers",
            "driver": {
                "key": "tests.fake-printers",
                "display_name": "Test Printer Driver",
                "kind": "printer",
                "platform": "test",
            },
            "connection": {
                "state": "online",
                "first_seen_at": _timestamp(),
                "last_seen_at": _timestamp(),
                "observed_at": _timestamp(),
            },
            "printer": {
                "is_default": name == "Kitchen Printer",
                "preferred_transport": "raw",
                "supported_transports": ["raw", "document"],
                "capabilities": ["cash_drawer"],
            },
            "metadata": {
                "source": "tests",
                "host": "printer.local",
                "port": 9100,
            },
        }
    )


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, 10, 0, tzinfo=UTC)
