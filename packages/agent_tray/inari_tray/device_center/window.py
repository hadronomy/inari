from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QItemSelectionModel, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QMenu,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from inari.models import DeviceResponse, RuntimeEventResponse
from inari.runtime.models import DeviceConnectionState

from ..models import TraySnapshot
from .chrome import ActivityOverlay
from .helpers import (
    compact_timestamp,
    device_endpoint,
    format_timestamp,
    pretty_json,
    string_value,
    yes_no,
)
from .table_models import (
    DEVICE_ROLE,
    DeviceNameDelegate,
    DeviceEventsTableModel,
    DeviceFilterProxyModel,
    DeviceStateBadgeDelegate,
    DeviceTableModel,
)
from .theme import resolve_device_center_theme


class DeviceCenterWindow(QMainWindow):
    refresh_requested = Signal()
    selection_changed = Signal(object)
    print_test_page_requested = Signal(object)
    open_cash_drawer_requested = Signal(object)
    copy_device_info_requested = Signal(object)
    pin_requested = Signal(object, bool)
    online_only_changed = Signal(bool)
    pinned_only_changed = Signal(bool)
    geometry_persist_requested = Signal(object)

    def __init__(self, *, title: str) -> None:
        super().__init__()
        self._devices: list[DeviceResponse] = []
        self._pinned_device_ids: set[str] = set()
        self._current_device: DeviceResponse | None = None
        self._current_events: tuple[RuntimeEventResponse, ...] = ()
        self._connected = True
        self._busy_message: str | None = None
        self._status_note: str | None = None
        self._status_note_mode = "ready"
        self._last_directory_updated_at: datetime | None = None
        self._shutdown_requested = False
        self._suppress_selection_signal = False
        self._theme = resolve_device_center_theme()
        self._device_model = DeviceTableModel()
        self._device_proxy = DeviceFilterProxyModel()
        self._device_proxy.setSourceModel(self._device_model)
        self._events_model = DeviceEventsTableModel()
        self._activity_overlay: ActivityOverlay | None = None
        self._status_note_timer = QTimer(self)
        self._status_note_timer.setSingleShot(True)
        self._status_note_timer.timeout.connect(self._clear_status_note)

        self.setObjectName("deviceCenterWindow")
        self.setWindowTitle(f"{title} Device Center")
        self.setMinimumSize(960, 640)
        self.resize(1200, 760)
        self.setStyleSheet(self._theme.application_style_sheet())

        self._root_widget = QWidget(self)
        self._root_widget.setObjectName("deviceCenterRoot")
        layout = QVBoxLayout(self._root_widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.setCentralWidget(self._root_widget)

        self._connection_banner = QLabel()
        self._connection_banner.setObjectName("connectionBanner")
        self._connection_banner.setWordWrap(True)
        self._connection_banner.setVisible(False)
        layout.addWidget(self._connection_banner)

        self._directory_empty_label = QLabel()
        self._directory_empty_label.setObjectName("emptyStateLabel")
        self._directory_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._directory_empty_label.setWordWrap(True)

        self._directory_empty_state_card = QFrame()
        self._directory_empty_state_card.setObjectName("emptyStateCard")
        directory_empty_layout = QVBoxLayout(self._directory_empty_state_card)
        directory_empty_layout.setContentsMargins(0, 0, 0, 0)
        directory_empty_layout.addWidget(self._directory_empty_label)

        self._inspector_empty_label = QLabel("No device details are available.")
        self._inspector_empty_label.setObjectName("emptyStateLabel")
        self._inspector_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._inspector_empty_label.setWordWrap(True)

        self._inspector_empty_state_card = QFrame()
        self._inspector_empty_state_card.setObjectName("emptyStateCard")
        inspector_empty_layout = QVBoxLayout(self._inspector_empty_state_card)
        inspector_empty_layout.setContentsMargins(0, 0, 0, 0)
        inspector_empty_layout.addWidget(self._inspector_empty_label)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        content_splitter.setHandleWidth(12)
        layout.addWidget(content_splitter, stretch=1)

        directory_pane = QWidget()
        directory_layout = QVBoxLayout(directory_pane)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        directory_layout.setSpacing(10)

        self._directory_header = QWidget()
        directory_layout.addWidget(self._directory_header)
        directory_header_layout = QVBoxLayout(self._directory_header)
        directory_header_layout.setContentsMargins(0, 0, 0, 0)
        directory_header_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        directory_header_layout.addLayout(header_row)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(2)
        self._page_title = QLabel("Devices")
        self._page_title.setObjectName("pageTitle")
        title_stack.addWidget(self._page_title)
        self._toolbar_meta_label = QLabel("Waiting for device data…")
        self._toolbar_meta_label.setObjectName("toolbarMeta")
        title_stack.addWidget(self._toolbar_meta_label)
        header_row.addLayout(title_stack, stretch=1)

        self._refresh_button = QPushButton("Refresh now")
        self._refresh_button.clicked.connect(self.refresh_requested)
        header_row.addWidget(self._refresh_button)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        directory_header_layout.addLayout(controls_row)

        search_panel = QWidget()
        search_panel.setObjectName("searchPanel")
        search_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        search_layout = QHBoxLayout(search_panel)
        search_layout.setContentsMargins(12, 0, 12, 0)
        search_layout.setSpacing(8)
        controls_row.addWidget(search_panel, stretch=1)

        search_label = QLabel("Search")
        search_label.setObjectName("searchPanelLabel")
        search_layout.addWidget(search_label)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("searchInput")
        self._search_input.setPlaceholderText(
            "Search by device, driver, source, or endpoint"
        )
        self._search_input.textChanged.connect(self._on_search_text_changed)
        search_layout.addWidget(self._search_input, stretch=1)

        self._clear_search_button = QPushButton("Clear")
        self._clear_search_button.setObjectName("searchClearButton")
        self._clear_search_button.clicked.connect(self._search_input.clear)
        self._clear_search_button.setVisible(False)
        search_layout.addWidget(self._clear_search_button)

        filter_group = QWidget()
        filter_group.setObjectName("searchFilterGroup")
        filter_layout = QHBoxLayout(filter_group)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        controls_row.addWidget(filter_group)

        self._online_only_button = QPushButton("Online")
        self._online_only_button.setCheckable(True)
        self._online_only_button.setProperty("buttonRole", "filter")
        self._online_only_button.toggled.connect(self._device_proxy.set_online_only)
        self._online_only_button.toggled.connect(self.online_only_changed)
        filter_layout.addWidget(self._online_only_button)

        self._pinned_only_button = QPushButton("Pinned")
        self._pinned_only_button.setCheckable(True)
        self._pinned_only_button.setProperty("buttonRole", "filter")
        self._pinned_only_button.toggled.connect(self._device_proxy.set_pinned_only)
        self._pinned_only_button.toggled.connect(self.pinned_only_changed)
        filter_layout.addWidget(self._pinned_only_button)

        self._directory_body = QWidget()
        directory_body_layout = QVBoxLayout(self._directory_body)
        directory_body_layout.setContentsMargins(0, 0, 0, 0)
        directory_body_layout.setSpacing(0)
        directory_layout.addWidget(self._directory_body, stretch=1)

        self._device_table = QTableView()
        self._device_table.setModel(self._device_proxy)
        self._device_table.setSortingEnabled(True)
        self._device_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._device_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._device_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._configure_device_table()
        self._device_table.setItemDelegateForColumn(
            0, DeviceNameDelegate(self._theme, self._device_table)
        )
        self._device_table.setItemDelegateForColumn(
            1, DeviceStateBadgeDelegate(self._theme, self._device_table)
        )
        self._device_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._device_table.selectionModel().selectionChanged.connect(
            self._emit_selection_changed
        )
        self._device_table.doubleClicked.connect(self._focus_inspector)
        self._device_table.customContextMenuRequested.connect(
            self._show_device_context_menu
        )
        self._device_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._table_frame = self._wrap_shell("tableShell", self._device_table)
        directory_body_layout.addWidget(self._directory_empty_state_card, stretch=1)
        directory_body_layout.addWidget(self._table_frame, stretch=1)
        content_splitter.addWidget(directory_pane)

        inspector = QFrame()
        inspector.setObjectName("inspectorCard")
        self._inspector_frame = inspector
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(14, 14, 14, 14)
        inspector_layout.setSpacing(10)

        self._device_title = QLabel("No device selected")
        self._device_title.setObjectName("deviceTitle")
        inspector_layout.addWidget(self._device_title)

        self._device_meta = QLabel("Choose a device from the table to inspect it.")
        self._device_meta.setObjectName("deviceMeta")
        self._device_meta.setWordWrap(True)
        inspector_layout.addWidget(self._device_meta)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        inspector_layout.addLayout(chip_row)

        self._device_status_chip = QLabel("No device")
        self._device_status_chip.setObjectName("deviceChip")
        chip_row.addWidget(self._device_status_chip)

        self._device_kind_chip = QLabel("—")
        self._device_kind_chip.setObjectName("deviceChip")
        chip_row.addWidget(self._device_kind_chip)

        self._device_class_chip = QLabel("—")
        self._device_class_chip.setObjectName("deviceChip")
        chip_row.addWidget(self._device_class_chip)

        self._device_default_chip = QLabel("Default")
        self._device_default_chip.setObjectName("deviceChip")
        chip_row.addWidget(self._device_default_chip)
        chip_row.addStretch(1)

        self._tabs = QTabWidget()
        inspector_layout.addWidget(self._tabs, stretch=1)

        self._inspector_body = QWidget()
        inspector_body_layout = QVBoxLayout(self._inspector_body)
        inspector_body_layout.setContentsMargins(0, 0, 0, 0)
        inspector_body_layout.setSpacing(0)
        inspector_body_layout.addWidget(self._inspector_empty_state_card, stretch=1)
        inspector_body_layout.addWidget(self._inspector_frame, stretch=1)
        content_splitter.addWidget(self._inspector_body)
        content_splitter.setStretchFactor(0, 5)
        content_splitter.setStretchFactor(1, 4)

        self._overview_fields, self._overview_metadata = self._build_overview_tab()
        self._driver_fields, self._driver_metadata = self._build_driver_tab()
        self._capability_fields = self._build_capabilities_tab()
        self._build_events_tab()

        self._device_proxy.modelReset.connect(self._update_content_state)
        self._device_proxy.layoutChanged.connect(self._update_content_state)
        self._device_proxy.rowsInserted.connect(lambda *_: self._update_content_state())
        self._device_proxy.rowsRemoved.connect(lambda *_: self._update_content_state())

        self.setStyleSheet(self._theme.application_style_sheet())
        self._activity_overlay = ActivityOverlay(self._theme, self._root_widget)
        self._update_content_state()
        self._set_current_device(None, ())
        self._refresh_toolbar_state()
        self._refresh_activity_overlay()
        self._update_overlay_geometry()

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_overlay_geometry()

    def restore_geometry_state(self, geometry: object | None) -> None:
        from .helpers import coerce_geometry

        data = coerce_geometry(geometry)
        if data is not None:
            self.restoreGeometry(data)

    def set_filter_state(self, *, online_only: bool, pinned_only: bool) -> None:
        self._set_toggle_state(self._online_only_button, online_only)
        self._set_toggle_state(self._pinned_only_button, pinned_only)
        self._device_proxy.set_online_only(online_only)
        self._device_proxy.set_pinned_only(pinned_only)

    def set_pinned_device_ids(self, pinned_device_ids: set[str]) -> None:
        self._pinned_device_ids = set(pinned_device_ids)
        self._device_model.set_pinned_device_ids(self._pinned_device_ids)
        self._device_proxy.set_pinned_device_ids(self._pinned_device_ids)
        self._update_summary_label()

    def set_connection_state(self, snapshot: TraySnapshot) -> None:
        self._connected = snapshot.connected
        if snapshot.connected:
            self._connection_banner.setVisible(False)
            self._refresh_toolbar_state()
            self._refresh_activity_overlay()
            return
        detail = snapshot.last_error or snapshot.control_line
        self._connection_banner.setText(
            f"{snapshot.headline}. Showing the last known device data if available.\n{detail}"
        )
        self._connection_banner.setVisible(True)
        self._refresh_toolbar_state()
        self._refresh_activity_overlay()

    def set_busy_message(self, message: str | None) -> None:
        self._busy_message = message
        self._refresh_toolbar_state()
        self._refresh_activity_overlay()

    def show_status_note(
        self,
        message: str,
        *,
        mode: str = "ready",
        timeout_ms: int = 4000,
    ) -> None:
        self._status_note = message.strip() or None
        self._status_note_mode = mode
        if self._status_note is None or timeout_ms <= 0:
            self._status_note_timer.stop()
        else:
            self._status_note_timer.start(timeout_ms)
        self._refresh_activity_overlay()

    def _on_search_text_changed(self, value: str) -> None:
        self._device_proxy.set_search_text(value)
        self._clear_search_button.setVisible(bool(value.strip()))

    def set_devices(
        self,
        devices: list[DeviceResponse],
        *,
        selected_device_id: str | None,
        pinned_device_ids: set[str],
    ) -> str | None:
        self._devices = list(devices)
        self._pinned_device_ids = set(pinned_device_ids)
        self._last_directory_updated_at = datetime.now().astimezone()
        previous_selected_id = self._selected_device_id()
        previous_scroll = self._device_table.verticalScrollBar().value()
        self._device_model.set_devices(
            self._devices, pinned_device_ids=pinned_device_ids
        )
        self._device_proxy.set_pinned_device_ids(self._pinned_device_ids)
        self._update_summary_label()
        self._update_content_state()

        self._suppress_selection_signal = True
        try:
            if self._device_proxy.rowCount() == 0:
                self._device_table.clearSelection()
                actual_selection = None
                return actual_selection
            target_device_id = selected_device_id or previous_selected_id
            actual_selection = self._selected_device_id()
            if actual_selection != target_device_id:
                actual_selection = self._select_device_by_id(target_device_id)
            if actual_selection is None and self._device_proxy.rowCount() > 0:
                actual_selection = self._select_device_by_proxy_row(0, reveal=False)
            if actual_selection is None:
                self._device_table.clearSelection()
        finally:
            self._suppress_selection_signal = False
        if actual_selection is not None and actual_selection == previous_selected_id:
            self._device_table.verticalScrollBar().setValue(previous_scroll)
        return actual_selection

    def set_device_details(
        self,
        device: DeviceResponse | None,
        events: list[RuntimeEventResponse] | tuple[RuntimeEventResponse, ...],
        *,
        pinned: bool,
    ) -> None:
        del pinned
        self._set_current_device(device, events)
        if device is None:
            return

        driver_name = device.driver.display_name if device.driver is not None else "—"
        source = string_value(device.metadata.get("source")).replace("_", " ").title()
        endpoint = device_endpoint(device)
        queue_name = string_value(device.metadata.get("queue_name"))
        host = string_value(device.metadata.get("host"))
        device_uri = string_value(device.metadata.get("device_uri"))
        supported_transport_labels = (
            [
                transport.value.upper()
                for transport in device.printer.supported_transports
            ]
            if device.printer is not None and device.printer.supported_transports
            else []
        )
        capability_labels = (
            [
                capability.value.replace("_", " ").title()
                for capability in device.printer.capabilities
            ]
            if device.printer is not None and device.printer.capabilities
            else []
        )

        self._set_field(self._overview_fields, "device_id", device.id)
        self._set_field(
            self._overview_fields, "state", device.connection.state.value.title()
        )
        self._set_field(
            self._overview_fields,
            "default",
            yes_no(device.printer is not None and device.printer.is_default),
        )
        self._set_field(
            self._overview_fields,
            "first_seen",
            format_timestamp(device.connection.first_seen_at),
        )
        self._set_field(
            self._overview_fields,
            "last_seen",
            format_timestamp(device.connection.last_seen_at),
        )
        self._set_field(
            self._overview_fields,
            "observed",
            format_timestamp(device.connection.observed_at),
        )
        self._set_field(self._overview_metadata_fields, "source", source or "—")
        self._set_field(
            self._overview_metadata_fields,
            "queue_name",
            queue_name or "—",
        )
        self._set_field(self._overview_metadata_fields, "host", host or "—")
        self._set_field(
            self._overview_metadata_fields,
            "endpoint",
            device_uri or endpoint,
        )
        self._overview_metadata.setPlainText(pretty_json(device.metadata))
        self._set_field(self._driver_fields, "driver_name", driver_name)
        self._set_field(self._driver_fields, "driver_key", device.driver_key)
        self._set_field(
            self._driver_fields,
            "driver_kind",
            device.driver.kind.value if device.driver is not None else "—",
        )
        self._set_field(
            self._driver_fields,
            "driver_platform",
            device.driver.platform if device.driver is not None else "—",
        )
        self._set_field(self._driver_fields, "source", source or "—")
        self._set_field(self._driver_fields, "endpoint", endpoint)
        self._driver_metadata.setPlainText(
            pretty_json(
                {
                    "driver": (
                        device.driver.model_dump(mode="json")
                        if device.driver is not None
                        else None
                    ),
                    "metadata": device.metadata,
                }
            )
        )
        self._set_field(
            self._capability_fields,
            "preferred_transport",
            (
                device.printer.preferred_transport.value.upper()
                if device.printer is not None
                and device.printer.preferred_transport is not None
                else "—"
            ),
        )
        self._set_field(
            self._capability_fields,
            "transport_count",
            str(len(supported_transport_labels)) if supported_transport_labels else "0",
        )
        self._set_field(
            self._capability_fields,
            "feature_count",
            str(len(capability_labels)) if capability_labels else "0",
        )
        self._set_field(
            self._capability_fields,
            "supported_transports",
            "\n".join(supported_transport_labels) if supported_transport_labels else "—",
        )
        self._set_field(
            self._capability_fields,
            "capabilities",
            "\n".join(capability_labels) if capability_labels else "—",
        )
        self._set_field(
            self._capability_fields,
            "cash_drawer",
            yes_no(
                device.printer is not None
                and any(
                    capability.value == "cash_drawer"
                    for capability in device.printer.capabilities
                )
            ),
        )
        self._events_model.set_events(events)
        self._events_summary_label.setText(
            f"Showing {len(events)} recent event{'s' if len(events) != 1 else ''}."
        )

    def begin_shutdown(self) -> None:
        self._shutdown_requested = True

    def closeEvent(self, event: QCloseEvent) -> None:
        self.geometry_persist_requested.emit(self.saveGeometry())
        if self._shutdown_requested:
            super().closeEvent(event)
            return
        self.hide()
        event.ignore()

    def _build_overview_tab(self) -> tuple[dict[str, QLabel], QPlainTextEdit]:
        layout = self._create_scroll_tab("Overview")

        metrics_layout = QGridLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setHorizontalSpacing(10)
        metrics_layout.setVerticalSpacing(10)
        metrics_layout.setColumnStretch(0, 1)
        metrics_layout.setColumnStretch(1, 1)
        state_card, state_value = self._build_metric_card("State")
        default_card, default_value = self._build_metric_card(
            "Default printer"
        )
        last_seen_card, last_seen_value = self._build_metric_card(
            "Last detected"
        )
        observed_card, observed_value = self._build_metric_card(
            "Last updated"
        )
        metrics_layout.addWidget(state_card, 0, 0)
        metrics_layout.addWidget(default_card, 0, 1)
        metrics_layout.addWidget(last_seen_card, 1, 0)
        metrics_layout.addWidget(observed_card, 1, 1)
        layout.addLayout(metrics_layout)

        identity_shell, identity_fields = self._build_detail_section(
            "Identity",
            (
                ("device_id", "Device ID"),
                ("first_seen", "First detected"),
            ),
            columns=1,
        )

        fields = {
            "state": state_value,
            "default": default_value,
            "last_seen": last_seen_value,
            "observed": observed_value,
        }
        fields.update(identity_fields)
        layout.addWidget(identity_shell)

        metadata_shell, self._overview_metadata_fields = self._build_detail_section(
            "Connection summary",
            (
                ("source", "Source"),
                ("queue_name", "Queue"),
                ("host", "Host"),
                ("endpoint", "Endpoint"),
            ),
            columns=2,
        )
        layout.addWidget(metadata_shell)

        disclosure_row = QHBoxLayout()
        disclosure_row.setSpacing(8)
        disclosure_row.addWidget(self._overview_section_title("Diagnostics"))
        disclosure_row.addStretch(1)
        self._overview_metadata_toggle = self._build_disclosure_button(
            "View raw metadata",
            "Hide raw metadata",
        )
        disclosure_row.addWidget(self._overview_metadata_toggle)
        layout.addLayout(disclosure_row)
        metadata = self._build_readonly_json_box(
            "Raw metadata and discovery details appear here."
        )
        self._overview_metadata_shell = self._wrap_shell("codeShell", metadata)
        self._overview_metadata_shell.setVisible(False)
        self._overview_metadata_toggle.toggled.connect(
            lambda checked: self._toggle_disclosure(
                self._overview_metadata_toggle,
                self._overview_metadata_shell,
                checked,
                show_text="View raw metadata",
                hide_text="Hide raw metadata",
            )
        )
        layout.addWidget(self._overview_metadata_shell)
        layout.addStretch(1)
        return fields, metadata

    def _build_driver_tab(self) -> tuple[dict[str, QLabel], QPlainTextEdit]:
        layout = self._create_scroll_tab("Driver")

        metrics_layout = QGridLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setHorizontalSpacing(10)
        metrics_layout.setVerticalSpacing(10)
        metrics_layout.setColumnStretch(0, 1)
        metrics_layout.setColumnStretch(1, 1)
        kind_card, kind_value = self._build_metric_card("Driver kind")
        platform_card, platform_value = self._build_metric_card("Platform")
        metrics_layout.addWidget(kind_card, 0, 0)
        metrics_layout.addWidget(platform_card, 0, 1)
        layout.addLayout(metrics_layout)

        profile_shell, profile_fields = self._build_detail_section(
            "Driver profile",
            (
                ("driver_name", "Driver"),
                ("driver_key", "Key"),
            ),
            columns=1,
        )
        layout.addWidget(profile_shell)

        routing_shell, routing_fields = self._build_detail_section(
            "Runtime path",
            (
                ("source", "Connected via"),
                ("endpoint", "Endpoint"),
            ),
            columns=2,
        )
        layout.addWidget(routing_shell)

        fields = {
            "driver_kind": kind_value,
            "driver_platform": platform_value,
        }
        fields.update(profile_fields)
        fields.update(routing_fields)
        self._driver_metadata_toggle = self._build_disclosure_button(
            "View raw driver payload",
            "Hide raw driver payload",
        )
        disclosure_row = QHBoxLayout()
        disclosure_row.setSpacing(8)
        disclosure_row.addWidget(self._overview_section_title("Diagnostics"))
        disclosure_row.addStretch(1)
        disclosure_row.addWidget(self._driver_metadata_toggle)
        layout.addLayout(disclosure_row)
        metadata = self._build_readonly_json_box(
            "Driver metadata and backend-specific details appear here."
        )
        self._driver_metadata_shell = self._wrap_shell("codeShell", metadata)
        self._driver_metadata_shell.setVisible(False)
        self._driver_metadata_toggle.toggled.connect(
            lambda checked: self._toggle_disclosure(
                self._driver_metadata_toggle,
                self._driver_metadata_shell,
                checked,
                show_text="View raw driver payload",
                hide_text="Hide raw driver payload",
            )
        )
        layout.addWidget(self._driver_metadata_shell)
        layout.addStretch(1)
        return fields, metadata

    def _build_capabilities_tab(self) -> dict[str, QLabel]:
        layout = self._create_scroll_tab("Capabilities")

        metrics_layout = QGridLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setHorizontalSpacing(10)
        metrics_layout.setVerticalSpacing(10)
        metrics_layout.setColumnStretch(0, 1)
        metrics_layout.setColumnStretch(1, 1)
        preferred_card, preferred_value = self._build_metric_card("Preferred transport")
        drawer_card, drawer_value = self._build_metric_card("Cash drawer")
        transport_count_card, transport_count_value = self._build_metric_card(
            "Transport count"
        )
        feature_count_card, feature_count_value = self._build_metric_card(
            "Feature count"
        )
        metrics_layout.addWidget(preferred_card, 0, 0)
        metrics_layout.addWidget(drawer_card, 0, 1)
        metrics_layout.addWidget(transport_count_card, 1, 0)
        metrics_layout.addWidget(feature_count_card, 1, 1)
        layout.addLayout(metrics_layout)

        transports_shell, transport_fields = self._build_detail_section(
            "Transport support",
            (("supported_transports", "Supported transports"),),
            columns=1,
        )
        layout.addWidget(transports_shell)

        features_shell, feature_fields = self._build_detail_section(
            "Reported features",
            (("capabilities", "Capabilities"),),
            columns=1,
        )
        layout.addWidget(features_shell)

        fields = {
            "preferred_transport": preferred_value,
            "cash_drawer": drawer_value,
            "transport_count": transport_count_value,
            "feature_count": feature_count_value,
        }
        fields.update(transport_fields)
        fields.update(feature_fields)
        layout.addStretch(1)
        return fields

    def _build_events_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)
        self._events_summary_label = QLabel("No recent activity loaded yet.")
        self._events_summary_label.setObjectName("eventsSummaryLabel")
        layout.addWidget(self._events_summary_label)

        self._events_table = QTableView()
        self._events_table.setModel(self._events_model)
        self._events_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._events_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._configure_events_table()
        layout.addWidget(self._wrap_shell("tableShell", self._events_table), stretch=1)
        self._tabs.addTab(tab, "Activity")

    def _build_readonly_json_box(self, placeholder: str) -> QPlainTextEdit:
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setFont(self._theme.code_font())
        box.setPlaceholderText(placeholder)
        box.setMinimumHeight(120)
        return box

    def _build_metric_card(self, label: str) -> tuple[QFrame, QLabel]:
        shell = QFrame()
        shell.setObjectName("overviewMetricCard")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        caption = QLabel(label)
        caption.setObjectName("overviewMetricLabel")
        layout.addWidget(caption)
        value = QLabel("—")
        value.setObjectName("overviewMetricValue")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(value)
        layout.addStretch(1)
        return shell, value

    def _build_detail_section(
        self,
        title: str,
        fields: tuple[tuple[str, str], ...],
        *,
        columns: int,
    ) -> tuple[QFrame, dict[str, QLabel]]:
        shell, layout = self._create_shell_layout("overviewSectionShell")
        layout.addWidget(self._overview_section_title(title))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for column in range(columns):
            grid.setColumnStretch(column, 1)

        value_fields: dict[str, QLabel] = {}
        for index, (key, label) in enumerate(fields):
            row = index // columns
            column = index % columns
            field_shell, value = self._build_detail_field_card(label)
            if key.endswith("_id") or key.endswith("_key"):
                value.setFont(self._theme.code_font())
            grid.addWidget(field_shell, row, column)
            value_fields[key] = value
        layout.addLayout(grid)
        return shell, value_fields

    def _build_detail_field_card(self, label: str) -> tuple[QFrame, QLabel]:
        shell = QFrame()
        shell.setObjectName("detailFieldCard")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        caption = QLabel(label)
        caption.setObjectName("detailFieldLabel")
        layout.addWidget(caption)
        value = QLabel("—")
        value.setObjectName("detailFieldValue")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(value)
        return shell, value

    def _create_scroll_tab(self, title: str) -> QVBoxLayout:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(14)

        scroll = QScrollArea()
        scroll.setObjectName("inspectorScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self._tabs.addTab(scroll, title)
        return layout

    def _overview_section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("overviewSectionTitle")
        return label

    def _build_disclosure_button(
        self,
        show_text: str,
        hide_text: str,
    ) -> QPushButton:
        button = QPushButton(show_text)
        button.setCheckable(True)
        button.setProperty("buttonRole", "subtle")
        button.setProperty("showText", show_text)
        button.setProperty("hideText", hide_text)
        return button

    def _wrap_shell(self, object_name: str, widget: QWidget) -> QFrame:
        shell, layout = self._create_shell_layout(object_name)
        layout.addWidget(widget)
        return shell

    def _create_shell_layout(self, object_name: str) -> tuple[QFrame, QVBoxLayout]:
        shell = QFrame()
        shell.setObjectName(object_name)
        layout = QVBoxLayout(shell)
        margins = 2 if object_name == "tableShell" else 16
        layout.setContentsMargins(margins, margins, margins, margins)
        layout.setSpacing(10)
        return shell, layout

    def _update_content_state(self) -> None:
        visible_rows = self._device_proxy.rowCount()
        self._table_frame.setVisible(visible_rows > 0)
        self._inspector_frame.setVisible(visible_rows > 0)
        self._directory_empty_state_card.setVisible(visible_rows == 0)
        self._inspector_empty_state_card.setVisible(visible_rows == 0)
        self._update_overlay_geometry()
        if visible_rows > 0:
            return
        if not self._devices:
            if self._connected:
                self._directory_empty_label.setText(
                    "No recognized devices yet.\nWhen Inari detects printers or other supported hardware, they will appear here."
                )
            else:
                self._directory_empty_label.setText(
                    "The tray cannot currently reach the local Inari API.\nReconnect the agent to load device information."
                )
            self._inspector_empty_label.setText(
                "No device details are available while the directory is empty."
            )
            return
        self._directory_empty_label.setText(
            "No devices match the current filters.\nTry clearing the search field or disabling a filter."
        )
        self._inspector_empty_label.setText(
            "No device details are available for the current filters."
        )

    def _update_summary_label(self) -> None:
        total = len(self._devices)
        online = sum(
            1
            for device in self._devices
            if device.connection.state is DeviceConnectionState.ONLINE
        )
        pinned = sum(
            1 for device in self._devices if device.id in self._pinned_device_ids
        )
        updated = (
            self._last_directory_updated_at.strftime("%H:%M:%S")
            if self._last_directory_updated_at is not None
            else "Waiting for first update"
        )
        self._toolbar_meta_label.setText(
            f"{total} devices · {online} online · {pinned} pinned · Updated {updated}"
            if self._last_directory_updated_at is not None
            else "Waiting for first device update"
        )

    def _update_overlay_geometry(self) -> None:
        if self._activity_overlay is None or not self._root_widget.isVisible():
            return
        max_width = max(self._root_widget.width(), 220)
        self._activity_overlay.setMaximumWidth(min(max_width, 280))
        self._activity_overlay.adjustSize()
        x = 0
        y = max(self._root_widget.height() - self._activity_overlay.height(), 0)
        self._activity_overlay.move(x, y)
        self._activity_overlay.raise_()

    def _refresh_activity_overlay(self) -> None:
        if self._activity_overlay is None:
            return
        mode, message = self._resolved_activity_status()
        self._activity_overlay.set_status(mode, message)
        self._activity_overlay.show()
        self._update_overlay_geometry()

    def _refresh_toolbar_state(self) -> None:
        if self._busy_message:
            self._refresh_button.setText("Refreshing…")
            self._refresh_button.setEnabled(False)
            return
        self._refresh_button.setEnabled(True)
        self._refresh_button.setText("Retry now" if not self._connected else "Refresh now")

    def _resolved_activity_status(self) -> tuple[str, str]:
        if self._status_note:
            return self._status_note_mode, self._status_note
        if self._busy_message:
            return "busy", self._busy_message
        if not self._connected:
            return "offline", "Disconnected · showing cached data"
        return "ready", "Live connection"

    def _clear_status_note(self) -> None:
        self._status_note = None
        self._status_note_mode = "ready"
        self._refresh_activity_overlay()

    def _set_current_device(
        self,
        device: DeviceResponse | None,
        events: list[RuntimeEventResponse] | tuple[RuntimeEventResponse, ...],
    ) -> None:
        self._current_device = device
        self._current_events = tuple(events)
        if device is None:
            self._device_title.setText("No device selected")
            self._device_meta.setText(
                "Choose a device from the table to inspect details, capabilities, and recent activity."
            )
            self._set_chip(self._device_status_chip, "No device", tone="neutral")
            self._set_chip(self._device_kind_chip, "—", tone="neutral")
            self._set_chip(self._device_class_chip, "—", tone="neutral")
            self._device_default_chip.setVisible(False)
            self._events_model.set_events(())
            self._events_summary_label.setText("No recent activity loaded yet.")
            self._overview_metadata.clear()
            self._driver_metadata.clear()
            for field_set in (
                self._overview_fields,
                self._driver_fields,
                self._capability_fields,
            ):
                for field in field_set.values():
                    field.setText("—")
            for field in self._overview_metadata_fields.values():
                field.setText("—")
            return

        driver_name = (
            device.driver.display_name
            if device.driver is not None
            else device.driver_key
        )
        kind_label = device.kind.value.replace("_", " ").title()
        class_label = device.device_class.value.replace("_", " ").title()
        self._device_title.setText(device.name)
        self._device_meta.setText(
            f"{driver_name} · Last detected {compact_timestamp(device.connection.last_seen_at)}"
        )
        self._set_chip(
            self._device_status_chip,
            device.connection.state.value.title(),
            tone=(
                "online"
                if device.connection.state is DeviceConnectionState.ONLINE
                else "offline"
            ),
        )
        self._set_chip(self._device_kind_chip, kind_label, tone="neutral")
        self._set_chip(self._device_class_chip, class_label, tone="neutral")
        self._set_chip(self._device_default_chip, "Default", tone="default")
        self._device_default_chip.setVisible(
            bool(device.printer is not None and device.printer.is_default)
        )

    def _configure_device_table(self) -> None:
        self._configure_table(self._device_table, compact=False, row_height=38)
        header = self._device_table.horizontalHeader()
        header.setMinimumSectionSize(64)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._device_table.setColumnWidth(0, 260)
        self._device_table.setColumnWidth(3, 220)

    def _configure_events_table(self) -> None:
        self._configure_table(self._events_table, compact=True, row_height=38)
        header = self._events_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

    def _configure_table(
        self,
        table: QTableView,
        *,
        compact: bool,
        row_height: int,
    ) -> None:
        table.setAlternatingRowColors(True)
        table.setMouseTracking(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        table.horizontalHeader().setHighlightSections(False)
        table.setStyleSheet(self._theme.table_style_sheet(compact=compact))

    def _emit_selection_changed(self, *_: object) -> None:
        if self._suppress_selection_signal:
            return
        if self._device_proxy.rowCount() == 0:
            self.selection_changed.emit(None)
            return
        selection_model = self._device_table.selectionModel()
        if selection_model is None:
            self.selection_changed.emit(None)
            return
        indexes = selection_model.selectedRows()
        if not indexes:
            self.selection_changed.emit(None)
            return
        device = indexes[0].data(DEVICE_ROLE)
        self.selection_changed.emit(
            device if isinstance(device, DeviceResponse) else None
        )

    def _show_device_context_menu(self, position) -> None:
        device = self._device_for_table_position(position)
        if device is None:
            return
        self._select_device_by_id(device.id)
        menu = QMenu(self._device_table)
        menu.setStyleSheet(self._theme.context_menu_style_sheet())

        refresh_action = menu.addAction("Refresh Device Data")
        print_action = menu.addAction("Print Test Page")
        drawer_action = menu.addAction("Open Cash Drawer")
        menu.addSeparator()
        pin_label = (
            "Unpin Device" if device.id in self._pinned_device_ids else "Pin Device"
        )
        pin_action = menu.addAction(pin_label)
        copy_action = menu.addAction("Copy Device Info")

        is_printer = device.printer is not None
        print_action.setEnabled(is_printer)
        drawer_action.setEnabled(self._device_supports_cash_drawer(device))

        chosen = menu.exec(self._device_table.viewport().mapToGlobal(position))
        if chosen is None:
            return
        if chosen == refresh_action:
            self.refresh_requested.emit()
            return
        if chosen == print_action:
            self.print_test_page_requested.emit(device)
            return
        if chosen == drawer_action:
            self.open_cash_drawer_requested.emit(device)
            return
        if chosen == pin_action:
            self.pin_requested.emit(
                device,
                device.id not in self._pinned_device_ids,
            )
            return
        if chosen == copy_action:
            self.copy_device_info_requested.emit(device)

    def _focus_inspector(self, *_: object) -> None:
        self._tabs.setFocus()

    def _device_for_table_position(self, position) -> DeviceResponse | None:
        index = self._device_table.indexAt(position)
        if not index.isValid():
            return None
        device = index.data(DEVICE_ROLE)
        if not isinstance(device, DeviceResponse):
            return None
        return device

    def _device_supports_cash_drawer(self, device: DeviceResponse) -> bool:
        return bool(
            device.printer is not None
            and any(
                capability.value == "cash_drawer"
                for capability in device.printer.capabilities
            )
        )

    def _set_toggle_state(self, button: QPushButton, value: bool) -> None:
        button.blockSignals(True)
        try:
            button.setChecked(value)
        finally:
            button.blockSignals(False)

    def _set_field(self, fields: dict[str, QLabel], key: str, value: str) -> None:
        fields[key].setText(value or "—")

    def _set_chip(self, label: QLabel, text: str, *, tone: str) -> None:
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def _toggle_disclosure(
        self,
        button: QPushButton,
        shell: QWidget,
        checked: bool,
        *,
        show_text: str,
        hide_text: str,
    ) -> None:
        shell.setVisible(checked)
        button.setText(hide_text if checked else show_text)
        shell.updateGeometry()
        parent = shell.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().activate()

    def _select_device_by_id(self, device_id: str | None) -> str | None:
        if not device_id:
            return None
        for row in range(self._device_proxy.rowCount()):
            index = self._device_proxy.index(row, 0)
            device = index.data(DEVICE_ROLE)
            if isinstance(device, DeviceResponse) and device.id == device_id:
                return self._select_device_by_proxy_row(row, reveal=False)
        return None

    def _select_device_by_proxy_row(self, row: int, *, reveal: bool) -> str | None:
        if row < 0 or row >= self._device_proxy.rowCount():
            return None
        index = self._device_proxy.index(row, 0)
        device = index.data(DEVICE_ROLE)
        if not isinstance(device, DeviceResponse):
            return None
        selection_model = self._device_table.selectionModel()
        if selection_model is not None:
            flags = (
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows
            )
            selection_model.setCurrentIndex(index, flags)
            selection_model.select(index, flags)
        if reveal:
            self._device_table.scrollTo(
                index,
                QAbstractItemView.ScrollHint.EnsureVisible,
            )
        return device.id

    def _selected_device_id(self) -> str | None:
        if self._device_proxy.rowCount() == 0:
            return None
        selection_model = self._device_table.selectionModel()
        if selection_model is None:
            return None
        indexes = selection_model.selectedRows()
        if not indexes:
            return None
        device = indexes[0].data(DEVICE_ROLE)
        if not isinstance(device, DeviceResponse):
            return None
        return device.id
