from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSize,
    Qt,
)
from PySide6.QtCore import QSortFilterProxyModel
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from inari.models import DeviceResponse, RuntimeEventResponse
from inari.runtime.models import DeviceConnectionState

from .helpers import compact_timestamp, device_endpoint
from .theme import DeviceCenterTheme

DISPLAY_ROLE = int(Qt.ItemDataRole.DisplayRole)
TOOLTIP_ROLE = int(Qt.ItemDataRole.ToolTipRole)
DEVICE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
EVENT_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class DeviceTableModel(QAbstractTableModel):
    HEADERS = (
        "Device",
        "Status",
        "Class",
        "Connected Via",
        "Last Seen",
    )

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[DeviceResponse] = []
        self._pinned_device_ids: set[str] = set()

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        if parent.isValid():
            return 0
        return len(self._devices)

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = DISPLAY_ROLE,
    ):
        if not index.isValid():
            return None
        device = self._devices[index.row()]
        if role == DEVICE_ROLE:
            return device
        if role == DISPLAY_ROLE:
            return self._display_value(device, index.column())
        if role == TOOLTIP_ROLE:
            return _device_tooltip(device, pinned=device.id in self._pinned_device_ids)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = DISPLAY_ROLE,
    ):
        if (
            orientation is Qt.Orientation.Horizontal
            and role == DISPLAY_ROLE
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def set_devices(
        self, devices: Sequence[DeviceResponse], *, pinned_device_ids: set[str]
    ) -> None:
        new_by_id = {device.id: device for device in devices}
        for row in range(len(self._devices) - 1, -1, -1):
            if self._devices[row].id in new_by_id:
                continue
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._devices[row]
            self.endRemoveRows()

        existing_ids = {device.id for device in self._devices}
        additions = [device for device in devices if device.id not in existing_ids]
        if additions:
            start = len(self._devices)
            end = start + len(additions) - 1
            self.beginInsertRows(QModelIndex(), start, end)
            self._devices.extend(additions)
            self.endInsertRows()

        changed_rows: set[int] = set()
        for row, current in enumerate(self._devices):
            updated = new_by_id.get(current.id)
            if updated is None or current == updated:
                continue
            self._devices[row] = updated
            changed_rows.add(row)

        if self._pinned_device_ids != set(pinned_device_ids):
            self._pinned_device_ids = set(pinned_device_ids)
            changed_rows.update(range(len(self._devices)))
        else:
            self._pinned_device_ids = set(pinned_device_ids)

        self._emit_changed_rows(changed_rows)

    def set_pinned_device_ids(self, pinned_device_ids: set[str]) -> None:
        self._pinned_device_ids = set(pinned_device_ids)
        if not self._devices:
            return
        top_left = self.index(0, 0)
        bottom_right = self.index(len(self._devices) - 1, len(self.HEADERS) - 1)
        self.dataChanged.emit(top_left, bottom_right)

    def device_at(self, row: int) -> DeviceResponse | None:
        if row < 0 or row >= len(self._devices):
            return None
        return self._devices[row]

    def _display_value(self, device: DeviceResponse, column: int) -> str:
        match column:
            case 0:
                return device.name
            case 1:
                return device.connection.state.value.title()
            case 2:
                return device.device_class.value.replace("_", " ").title()
            case 3:
                return _device_source_label(device)
            case 4:
                return compact_timestamp(device.connection.last_seen_at)
            case _:
                return ""

    def _emit_changed_rows(self, rows: Iterable[int]) -> None:
        ordered_rows = sorted(set(rows))
        if not ordered_rows:
            return
        start = ordered_rows[0]
        end = start
        for row in ordered_rows[1:]:
            if row == end + 1:
                end = row
                continue
            self._emit_data_changed(start, end)
            start = row
            end = row
        self._emit_data_changed(start, end)

    def _emit_data_changed(self, start_row: int, end_row: int) -> None:
        top_left = self.index(start_row, 0)
        bottom_right = self.index(end_row, len(self.HEADERS) - 1)
        self.dataChanged.emit(top_left, bottom_right)


class DeviceFilterProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self._search_text = ""
        self._online_only = False
        self._pinned_only = False
        self._pinned_device_ids: set[str] = set()
        self.setDynamicSortFilter(True)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_search_text(self, value: str) -> None:
        normalized = value.strip().casefold()
        if normalized == self._search_text:
            return
        self._search_text = normalized
        self.invalidateFilter()

    def set_online_only(self, value: bool) -> None:
        if value == self._online_only:
            return
        self._online_only = value
        self.invalidateFilter()

    def set_pinned_only(self, value: bool) -> None:
        if value == self._pinned_only:
            return
        self._pinned_only = value
        self.invalidateFilter()

    def set_pinned_device_ids(self, pinned_device_ids: set[str]) -> None:
        self._pinned_device_ids = set(pinned_device_ids)
        self.invalidate()

    def lessThan(
        self,
        left: QModelIndex | QPersistentModelIndex,
        right: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        left_device = left.data(DEVICE_ROLE)
        right_device = right.data(DEVICE_ROLE)
        if isinstance(left_device, DeviceResponse) and isinstance(
            right_device, DeviceResponse
        ):
            left_pinned = left_device.id in self._pinned_device_ids
            right_pinned = right_device.id in self._pinned_device_ids
            if left_pinned != right_pinned:
                return left_pinned
            match left.column():
                case 1:
                    left_online = (
                        left_device.connection.state is DeviceConnectionState.ONLINE
                    )
                    right_online = (
                        right_device.connection.state is DeviceConnectionState.ONLINE
                    )
                    if left_online != right_online:
                        return left_online
                case 4:
                    return (
                        left_device.connection.last_seen_at
                        < right_device.connection.last_seen_at
                    )
        return super().lessThan(left, right)

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        model = self.sourceModel()
        if not isinstance(model, DeviceTableModel):
            return True
        device = model.device_at(source_row)
        if device is None:
            return False
        if (
            self._online_only
            and device.connection.state is not DeviceConnectionState.ONLINE
        ):
            return False
        if self._pinned_only and device.id not in self._pinned_device_ids:
            return False
        if not self._search_text:
            return True
        haystack = " ".join(
            filter(
                None,
                (
                    device.name,
                    device.kind.value,
                    device.driver.display_name if device.driver is not None else "",
                    device.driver.platform if device.driver is not None else "",
                    device.driver_key,
                    device.metadata.get("source", ""),
                    device.metadata.get("host", ""),
                    device.metadata.get("device_uri", ""),
                ),
            )
        ).casefold()
        return self._search_text in haystack


class DeviceEventsTableModel(QAbstractTableModel):
    HEADERS = ("When", "Type", "Detail")

    def __init__(self) -> None:
        super().__init__()
        self._events: list[RuntimeEventResponse] = []

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        if parent.isValid():
            return 0
        return len(self._events)

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = DISPLAY_ROLE,
    ):
        if not index.isValid():
            return None
        event = self._events[index.row()]
        if role == EVENT_ROLE:
            return event
        if role == DISPLAY_ROLE:
            match index.column():
                case 0:
                    return event.occurred_at.astimezone(UTC).strftime(
                        "%Y-%m-%d %H:%M:%S UTC"
                    )
                case 1:
                    return event.event_type
                case 2:
                    return _event_detail(event)
        if role == TOOLTIP_ROLE:
            return _event_tooltip(event)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = DISPLAY_ROLE,
    ):
        if (
            orientation is Qt.Orientation.Horizontal
            and role == DISPLAY_ROLE
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def set_events(self, events: Sequence[RuntimeEventResponse]) -> None:
        self.beginResetModel()
        self._events = list(events)
        self.endResetModel()


class DeviceStateBadgeDelegate(QStyledItemDelegate):
    def __init__(
        self,
        theme: DeviceCenterTheme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        device = index.data(DEVICE_ROLE)
        if not isinstance(device, DeviceResponse):
            super().paint(painter, option, index)
            return

        base_option = QStyleOptionViewItem(option)
        self.initStyleOption(base_option, index)
        base_option.text = ""
        style = (
            base_option.widget.style()
            if base_option.widget is not None
            else QApplication.style()
        )
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            base_option,
            painter,
            base_option.widget,
        )

        state = device.connection.state
        label = "Online" if state is DeviceConnectionState.ONLINE else "Offline"
        dot = (
            self._theme.color(self._theme.success_foreground)
            if state is DeviceConnectionState.ONLINE
            else self._theme.color(self._theme.offline_foreground)
        )
        text = (
            self._theme.color(self._theme.text_secondary)
            if state is DeviceConnectionState.ONLINE
            else self._theme.color(self._theme.offline_foreground)
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = painter.font()
        font.setBold(False)
        font.setPointSize(max(font.pointSize() - 1, 9))
        painter.setFont(font)
        dot_size = 7
        gap = 8
        content_rect = option.rect.adjusted(12, 0, -12, 0)
        dot_x = content_rect.left()
        dot_y = content_rect.center().y() - (dot_size // 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(dot_x, dot_y, dot_size, dot_size)
        text_rect = content_rect.adjusted(dot_size + gap, 0, 0, 0)
        if bool(option.state & QStyle.StateFlag.State_Selected):
            text = self._theme.color(self._theme.text_primary)
        painter.setPen(QPen(text))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )
        painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        base = super().sizeHint(option, index)
        return QSize(base.width(), max(base.height(), 24))


class DeviceNameDelegate(QStyledItemDelegate):
    def __init__(
        self,
        theme: DeviceCenterTheme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        if not bool(option.state & QStyle.StateFlag.State_Selected):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent_rect = option.rect.adjusted(8, 6, -option.rect.width() + 11, -6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._theme.color(self._theme.accent))
        painter.drawRoundedRect(accent_rect, 2, 2)
        painter.restore()


def _event_detail(event: RuntimeEventResponse) -> str:
    payload = event.payload
    for key in ("error_detail", "name", "device_name", "job_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return event.resource_id


def _device_tooltip(device: DeviceResponse, *, pinned: bool) -> str:
    parts = [
        device.name,
        f"State: {device.connection.state.value}",
        f"Connected via: {_device_source_label(device)}",
    ]
    if pinned:
        parts.append("Pinned")
    endpoint = device_endpoint(device)
    if endpoint != "—":
        parts.append(f"Endpoint: {endpoint}")
    source = device.metadata.get("source")
    if isinstance(source, str) and source:
        parts.append(f"Source: {source}")
    return "\n".join(parts)


def _device_source_label(device: DeviceResponse) -> str:
    if device.driver is not None:
        return device.driver.display_name
    source = device.metadata.get("source")
    if isinstance(source, str) and source:
        return source.replace("_", " ").title()
    return device.driver_key


def _event_tooltip(event: RuntimeEventResponse) -> str:
    return "\n".join(
        (
            event.event_type,
            event.occurred_at.astimezone(UTC).isoformat(),
            _event_detail(event),
        )
    )
