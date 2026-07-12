from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from inari.local_api.schemas import (
    DeviceResponse,
    ManagedOnboardingPreviewResponse,
    ManagedOnboardingStartResponse,
    ManagedOnboardingStatusResponse,
)
from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import TraySettings
from ..device_center.theme import resolve_device_center_theme
from .state import SetupStep, step_for_phase


class SetupClient(Protocol):
    def preview_onboarding(
        self, invitation: str, *, controller_url: str | None = None
    ) -> ManagedOnboardingPreviewResponse: ...

    def start_onboarding(
        self, invitation: str, *, controller_url: str | None = None
    ) -> ManagedOnboardingStartResponse: ...

    def get_onboarding_status(self) -> ManagedOnboardingStatusResponse: ...

    def confirm_onboarding_devices(
        self,
        *,
        device_ids: tuple[str, ...],
        labels: dict[str, str],
        default_printer_device_id: str | None,
    ) -> ManagedOnboardingStatusResponse: ...

    def cancel_onboarding(self) -> ManagedOnboardingStatusResponse: ...

    def submit_test_page(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> object: ...


class SetupBridge(Protocol):
    def restart(self) -> str: ...


class _WorkerSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)


class SetupAssistantWindow(QMainWindow):
    def __init__(
        self,
        settings: TraySettings,
        *,
        client: SetupClient,
        bridge: SetupBridge,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.client = client
        self.bridge = bridge
        self.on_ready = on_ready
        self._signals = _WorkerSignals()
        self._signals.completed.connect(self._worker_completed)
        self._signals.failed.connect(self._worker_failed)
        self._poll_in_flight = False
        self._device_rows: list[
            tuple[DeviceResponse, QCheckBox, QLineEdit, QRadioButton]
        ] = []
        self._default_group = QButtonGroup(self)
        self._default_group.setExclusive(True)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_status)
        self._build_ui()

    def show_with_invitation(self, invitation: str | None = None) -> None:
        if invitation:
            self.invitation_input.setPlainText(invitation)
        self.show()
        self.raise_()
        self.activateWindow()

    def _build_ui(self) -> None:
        self.setWindowTitle("Connect to Inari")
        self.setMinimumSize(760, 540)
        self.resize(820, 580)
        self.setObjectName("setupAssistantWindow")
        theme = resolve_device_center_theme()
        self.setStyleSheet(_style_sheet(theme))

        root = QWidget()
        root.setObjectName("setupRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_progress_panel())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_invitation_page())
        self.pages.addWidget(self._build_connection_page())
        self.pages.addWidget(self._build_devices_page())
        self.pages.addWidget(self._build_ready_page())
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

    def _build_progress_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("setupProgressPanel")
        panel.setFixedWidth(210)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 28, 20, 24)
        layout.setSpacing(10)
        mark = QLabel("I")
        mark.setObjectName("setupMark")
        mark.setFixedSize(38, 38)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mark)
        title = QLabel("Set up Inari")
        title.setObjectName("setupPanelTitle")
        layout.addWidget(title)
        layout.addSpacing(18)
        self._progress_labels: dict[SetupStep, QLabel] = {}
        for step, text in (
            (SetupStep.CHECKING, "Checking this computer"),
            (SetupStep.SECURING, "Securing the connection"),
            (SetupStep.CONNECTING, "Connecting to Inari"),
            (SetupStep.DEVICES, "Finding devices"),
            (SetupStep.READY, "Ready"),
        ):
            label = QLabel(text)
            label.setObjectName("setupProgressItem")
            label.setProperty("state", "pending")
            label.setWordWrap(True)
            layout.addWidget(label)
            self._progress_labels[step] = label
        layout.addStretch()
        privacy = QLabel("Your invitation is kept in the local secure store.")
        privacy.setObjectName("setupPrivacy")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        return panel

    def _build_page_shell(self, title: str, body: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(48, 42, 48, 36)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("setupTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        description = QLabel(body)
        description.setObjectName("setupBody")
        description.setWordWrap(True)
        layout.addWidget(description)
        return page, layout

    def _build_invitation_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "Connect this computer",
            "Paste the invitation link or enter the code provided by your Inari administrator.",
        )
        layout.addSpacing(10)
        label = QLabel("Invitation")
        label.setObjectName("setupFieldLabel")
        layout.addWidget(label)
        self.invitation_input = QPlainTextEdit()
        self.invitation_input.setObjectName("setupInvitationInput")
        self.invitation_input.setPlaceholderText("Invitation link or INR code")
        self.invitation_input.setMaximumHeight(112)
        layout.addWidget(self.invitation_input)

        self.server_toggle = QToolButton()
        self.server_toggle.setObjectName("setupDisclosure")
        self.server_toggle.setText("Use a server address")
        self.server_toggle.setCheckable(True)
        self.server_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.server_toggle.toggled.connect(self._toggle_server_field)
        layout.addWidget(self.server_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.server_field = QLineEdit()
        self.server_field.setObjectName("setupServerInput")
        self.server_field.setPlaceholderText("https://inari.example.com")
        self.server_field.setVisible(False)
        layout.addWidget(self.server_field)
        self.invitation_error = QLabel("")
        self.invitation_error.setObjectName("setupError")
        self.invitation_error.setWordWrap(True)
        self.invitation_error.setVisible(False)
        layout.addWidget(self.invitation_error)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("setupPrimaryButton")
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self._begin_setup)
        actions.addWidget(self.connect_button)
        layout.addLayout(actions)
        return page

    def _build_connection_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "Making a secure connection",
            "This usually takes less than a minute. Inari will restart quietly if needed.",
        )
        layout.addSpacing(14)
        self.connection_status = QLabel("Checking this computer")
        self.connection_status.setObjectName("setupStatus")
        self.connection_status.setWordWrap(True)
        layout.addWidget(self.connection_status)
        self.connection_progress = QProgressBar()
        self.connection_progress.setRange(0, 0)
        self.connection_progress.setTextVisible(False)
        self.connection_progress.setFixedHeight(6)
        layout.addWidget(self.connection_progress)
        self.connection_error = QLabel("")
        self.connection_error.setObjectName("setupError")
        self.connection_error.setWordWrap(True)
        self.connection_error.setVisible(False)
        layout.addWidget(self.connection_error)
        self.details_toggle = QToolButton()
        self.details_toggle.setObjectName("setupDisclosure")
        self.details_toggle.setText("Technical details")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.toggled.connect(self._toggle_details)
        layout.addWidget(self.details_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.details_panel = QFrame()
        self.details_panel.setObjectName("setupDetails")
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(14, 12, 14, 12)
        details_layout.setSpacing(6)
        self.detail_labels: dict[str, QLabel] = {}
        for key, title in (
            ("controller", "Server"),
            ("agent", "Agent"),
            ("protocol", "Protocol"),
            ("namespace", "Zenoh namespace"),
            ("certificate", "Certificate"),
        ):
            label = QLabel(f"{title}: -")
            label.setObjectName("setupDetailLine")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            details_layout.addWidget(label)
            self.detail_labels[key] = label
        self.details_panel.setVisible(False)
        layout.addWidget(self.details_panel)
        layout.addStretch()
        actions = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("setupSecondaryButton")
        self.cancel_button.clicked.connect(self._cancel_setup)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        self.retry_button = QPushButton("Try Again")
        self.retry_button.setObjectName("setupPrimaryButton")
        self.retry_button.clicked.connect(self._begin_setup)
        self.retry_button.setVisible(False)
        actions.addWidget(self.retry_button)
        layout.addLayout(actions)
        return page

    def _build_devices_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "Choose your devices",
            "Confirm what should be available through Inari. Friendly labels stay local to Inari.",
        )
        self.device_scroll = QScrollArea()
        self.device_scroll.setObjectName("setupDeviceScroll")
        self.device_scroll.setWidgetResizable(True)
        self.device_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.device_list = QWidget()
        self.device_list_layout = QVBoxLayout(self.device_list)
        self.device_list_layout.setContentsMargins(0, 0, 0, 0)
        self.device_list_layout.setSpacing(8)
        self.device_scroll.setWidget(self.device_list)
        layout.addWidget(self.device_scroll, 1)
        actions = QHBoxLayout()
        self.rescan_button = QPushButton("Refresh")
        self.rescan_button.setObjectName("setupSecondaryButton")
        self.rescan_button.clicked.connect(self._poll_status)
        actions.addWidget(self.rescan_button)
        actions.addStretch()
        self.confirm_button = QPushButton("Continue")
        self.confirm_button.setObjectName("setupPrimaryButton")
        self.confirm_button.clicked.connect(self._confirm_devices)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)
        return page

    def _build_ready_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "You're ready",
            "This computer and its devices are securely connected to Inari.",
        )
        layout.addSpacing(20)
        ready_mark = QLabel("\u2713")
        ready_mark.setObjectName("setupReadyMark")
        ready_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ready_mark.setFixedSize(58, 58)
        layout.addWidget(ready_mark, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.ready_summary = QLabel("")
        self.ready_summary.setObjectName("setupStatus")
        self.ready_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ready_summary.setWordWrap(True)
        layout.addWidget(self.ready_summary)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        finish = QPushButton("Open Device Center")
        finish.setObjectName("setupPrimaryButton")
        finish.clicked.connect(self._finish)
        actions.addWidget(finish)
        layout.addLayout(actions)
        return page

    def _begin_setup(self) -> None:
        invitation = self.invitation_input.toPlainText().strip()
        if not invitation:
            self._show_invitation_error("Enter your invitation link or code.")
            return
        self._show_invitation_error(None)
        self.pages.setCurrentIndex(1)
        self._set_step(SetupStep.CHECKING)
        self.connection_status.setText("Checking this computer")
        self.connection_error.setVisible(False)
        self.retry_button.setVisible(False)
        self.connect_button.setEnabled(False)
        server = self.server_field.text().strip() or None
        self._run(
            "preview",
            lambda: self.client.preview_onboarding(invitation, controller_url=server),
        )

    def _start_after_preview(self, preview: ManagedOnboardingPreviewResponse) -> None:
        self._update_details(preview)
        invitation = self.invitation_input.toPlainText().strip()
        server = self.server_field.text().strip() or None
        self._set_step(SetupStep.SECURING)
        self.connection_status.setText("Securing the connection")
        self._run(
            "start",
            lambda: self.client.start_onboarding(invitation, controller_url=server),
        )

    def _start_polling(self) -> None:
        self._poll_timer.start()
        self._poll_status()

    def _poll_status(self) -> None:
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        self._run("status", self.client.get_onboarding_status)

    def _apply_status(self, status: ManagedOnboardingStatusResponse) -> None:
        self._update_details(status)
        step = step_for_phase(status.phase, has_devices=bool(status.devices))
        self._set_step(step)
        self.connection_status.setText(status.detail)
        if step is SetupStep.FAILED:
            self._poll_timer.stop()
            self._show_connection_error(
                status.last_error
                or status.detail
                or "The connection could not be completed."
            )
            return
        if step is SetupStep.DEVICES and status.devices:
            self._poll_timer.stop()
            self._populate_devices(status.devices)
            self.pages.setCurrentIndex(2)
            return
        if step is SetupStep.READY:
            self._poll_timer.stop()
            self._show_ready(status)

    def _populate_devices(self, devices: list[DeviceResponse]) -> None:
        while self.device_list_layout.count():
            item = self.device_list_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._device_rows.clear()
        self._default_group = QButtonGroup(self)
        self._default_group.setExclusive(True)
        first_printer_radio: QRadioButton | None = None
        for device in devices:
            row = QFrame()
            row.setObjectName("setupDeviceRow")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(14, 12, 14, 12)
            row_layout.setSpacing(8)
            top = QHBoxLayout()
            selected = QCheckBox(device.name)
            selected.setChecked(True)
            selected.setObjectName("setupDeviceCheck")
            top.addWidget(selected, 1)
            state = QLabel(device.connection.state.value.capitalize())
            state.setObjectName("setupDeviceState")
            top.addWidget(state)
            row_layout.addLayout(top)
            label_input = QLineEdit()
            label_input.setPlaceholderText("Friendly label (optional)")
            row_layout.addWidget(label_input)
            printer_row = QHBoxLayout()
            default_radio = QRadioButton("Default printer")
            default_radio.setVisible(device.kind.value == "printer")
            self._default_group.addButton(default_radio)
            printer_row.addWidget(default_radio)
            if device.kind.value == "printer":
                if first_printer_radio is None:
                    first_printer_radio = default_radio
                if device.printer is not None and device.printer.is_default:
                    default_radio.setChecked(True)
                test_button = QPushButton("Test")
                test_button.setObjectName("setupTertiaryButton")
                test_button.clicked.connect(
                    lambda checked=False, device_id=device.id: self._test_device(
                        device_id
                    )
                )
                printer_row.addWidget(test_button)
            printer_row.addStretch()
            row_layout.addLayout(printer_row)
            selected.toggled.connect(default_radio.setEnabled)
            self.device_list_layout.addWidget(row)
            self._device_rows.append((device, selected, label_input, default_radio))
        if (
            first_printer_radio is not None
            and self._default_group.checkedButton() is None
        ):
            first_printer_radio.setChecked(True)
        self.device_list_layout.addStretch()

    def _confirm_devices(self) -> None:
        device_ids = tuple(
            device.id
            for device, selected, _, _ in self._device_rows
            if selected.isChecked()
        )
        labels = {
            device.id: label.text().strip()
            for device, selected, label, _ in self._device_rows
            if selected.isChecked() and label.text().strip()
        }
        default_device_id = next(
            (
                device.id
                for device, selected, _, default in self._device_rows
                if selected.isChecked() and default.isChecked()
            ),
            None,
        )
        self.confirm_button.setEnabled(False)
        self._run(
            "confirm",
            lambda: self.client.confirm_onboarding_devices(
                device_ids=device_ids,
                labels=labels,
                default_printer_device_id=default_device_id,
            ),
        )

    def _test_device(self, device_id: str) -> None:
        self._run(
            "test",
            lambda: self.client.submit_test_page(device_id=device_id),
        )

    def _cancel_setup(self) -> None:
        self._poll_timer.stop()
        self.cancel_button.setEnabled(False)
        self._run("cancel", self.client.cancel_onboarding)

    def _finish(self) -> None:
        self.close()
        if self.on_ready is not None:
            self.on_ready()

    def _worker_completed(self, operation: str, result: object) -> None:
        if operation == "preview" and isinstance(
            result, ManagedOnboardingPreviewResponse
        ):
            self._start_after_preview(result)
            return
        if operation == "start" and isinstance(result, ManagedOnboardingStartResponse):
            self._update_details(result)
            if result.restart_required:
                self.connection_status.setText("Restarting Inari securely")
                self._run("restart", self.bridge.restart)
            else:
                self._start_polling()
            return
        if operation == "restart":
            self._start_polling()
            return
        if operation == "status" and isinstance(
            result, ManagedOnboardingStatusResponse
        ):
            self._poll_in_flight = False
            self._apply_status(result)
            return
        if operation == "confirm" and isinstance(
            result, ManagedOnboardingStatusResponse
        ):
            self.confirm_button.setEnabled(True)
            self._show_ready(result)
            return
        if operation == "cancel":
            self.close()

    def _worker_failed(self, operation: str, message: str) -> None:
        if operation == "status":
            self._poll_in_flight = False
            return
        if operation == "confirm":
            self.confirm_button.setEnabled(True)
            return
        self.connect_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self._poll_timer.stop()
        self._set_step(SetupStep.FAILED)
        self._show_connection_error(message)

    def _run(self, operation: str, task: Callable[[], object]) -> None:
        def runner() -> None:
            try:
                result = task()
            except Exception as exc:
                self._signals.failed.emit(operation, _friendly_error(exc))
                return
            self._signals.completed.emit(operation, result)

        threading.Thread(
            target=runner,
            name=f"inari-setup-{operation}",
            daemon=True,
        ).start()

    def _set_step(self, active: SetupStep) -> None:
        ordered = [
            SetupStep.CHECKING,
            SetupStep.SECURING,
            SetupStep.CONNECTING,
            SetupStep.DEVICES,
            SetupStep.READY,
        ]
        active_index = ordered.index(active) if active in ordered else -1
        for index, step in enumerate(ordered):
            label = self._progress_labels[step]
            state = (
                "done"
                if active_index >= 0 and index < active_index
                else "active"
                if step is active
                else "pending"
            )
            label.setProperty("state", state)
            label.style().unpolish(label)
            label.style().polish(label)

    def _update_details(self, value: object) -> None:
        controller_url = getattr(value, "controller_url", None)
        controller_name = getattr(value, "controller_name", None)
        agent_id = getattr(value, "agent_id", None)
        protocol = getattr(value, "protocol_version", None)
        namespace = getattr(value, "zenoh_namespace", None)
        certificate = getattr(value, "certificate_expires_at", None)
        self.detail_labels["controller"].setText(
            f"Server: {controller_name or controller_url or '-'}"
        )
        self.detail_labels["agent"].setText(f"Agent: {agent_id or '-'}")
        self.detail_labels["protocol"].setText(f"Protocol: {protocol or '-'}")
        self.detail_labels["namespace"].setText(f"Zenoh namespace: {namespace or '-'}")
        self.detail_labels["certificate"].setText(
            "Certificate: "
            + (certificate.isoformat() if certificate is not None else "-")
        )

    def _show_ready(self, status: ManagedOnboardingStatusResponse) -> None:
        self._set_step(SetupStep.READY)
        count = len(status.devices)
        self.ready_summary.setText(
            f"Connected {count} device{'s' if count != 1 else ''}."
            if count
            else "Connected. You can add devices at any time."
        )
        self.pages.setCurrentIndex(3)

    def _show_invitation_error(self, message: str | None) -> None:
        self.invitation_error.setText(message or "")
        self.invitation_error.setVisible(bool(message))

    def _show_connection_error(self, message: str) -> None:
        self.pages.setCurrentIndex(1)
        self.connection_error.setText(message)
        self.connection_error.setVisible(True)
        self.connection_progress.setVisible(False)
        self.retry_button.setVisible(True)

    def _toggle_server_field(self, visible: bool) -> None:
        self.server_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )
        self.server_field.setVisible(visible)

    def _toggle_details(self, visible: bool) -> None:
        self.details_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )
        self.details_panel.setVisible(visible)

    def dismiss(self) -> None:
        self.close()


def create_setup_assistant(
    settings: TraySettings,
    *,
    client: SetupClient,
    bridge: SetupBridge,
    on_ready: Callable[[], None] | None = None,
) -> SetupAssistantWindow:
    return SetupAssistantWindow(
        settings,
        client=client,
        bridge=bridge,
        on_ready=on_ready,
    )


def should_offer_first_run_setup() -> bool:
    settings = QSettings("Inari", "Inari Tray")
    return not settings.value("setup_assistant_offered", False, type=bool)


def mark_first_run_setup_offered() -> None:
    settings = QSettings("Inari", "Inari Tray")
    settings.setValue("setup_assistant_offered", True)


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            payload = exc.response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail")
        if not isinstance(detail, str):
            detail = (payload.get("error") or {}).get("message")
        if isinstance(detail, str) and detail:
            return detail
    message = str(exc).strip()
    return message or "Inari could not complete this step."


def _style_sheet(theme: Any) -> str:
    return f"""
    QMainWindow#setupAssistantWindow, QWidget#setupRoot {{
        background: {theme.background};
        color: {theme.text_primary};
    }}
    QFrame#setupProgressPanel {{
        background: {theme.surface_alt};
        border-right: 1px solid {theme.border};
    }}
    QLabel#setupMark {{
        background: {theme.accent};
        color: {theme.accent_foreground};
        border-radius: 8px;
        font-size: 18px;
        font-weight: 800;
    }}
    QLabel#setupPanelTitle {{ font-size: 15px; font-weight: 700; }}
    QLabel#setupTitle {{ font-size: 25px; font-weight: 750; }}
    QLabel#setupBody {{
        color: {theme.text_secondary};
        font-size: 14px;
    }}
    QLabel#setupFieldLabel {{
        color: {theme.text_secondary};
        font-size: 12px;
        font-weight: 700;
    }}
    QLabel#setupPrivacy {{
        color: {theme.text_muted};
        font-size: 11px;
    }}
    QLabel#setupProgressItem {{
        color: {theme.text_muted};
        padding: 7px 0;
        font-size: 12px;
    }}
    QLabel#setupProgressItem[state="active"] {{
        color: {theme.text_primary};
        font-weight: 700;
    }}
    QLabel#setupProgressItem[state="done"] {{
        color: {theme.success_foreground};
        font-weight: 650;
    }}
    QLabel#setupStatus {{
        color: {theme.text_primary};
        font-size: 14px;
        font-weight: 650;
    }}
    QLabel#setupError {{
        background: {theme.offline_background};
        color: {theme.offline_foreground};
        border: 1px solid {theme.offline_foreground};
        border-radius: 6px;
        padding: 10px;
    }}
    QLabel#setupDetailLine {{
        color: {theme.text_secondary};
        font-family: monospace;
        font-size: 11px;
    }}
    QLabel#setupReadyMark {{
        background: {theme.success_background};
        color: {theme.success_foreground};
        border: 1px solid {theme.success_foreground};
        border-radius: 8px;
        font-size: 28px;
        font-weight: 800;
    }}
    QLabel#setupDeviceState {{
        color: {theme.text_muted};
        font-size: 11px;
    }}
    QPlainTextEdit#setupInvitationInput, QLineEdit#setupServerInput,
    QFrame#setupDetails, QFrame#setupDeviceRow {{
        background: {theme.input_background};
        color: {theme.text_primary};
        border: 1px solid {theme.border};
        border-radius: 7px;
    }}
    QLineEdit {{
        min-height: 34px;
        padding: 0 10px;
        background: {theme.input_background};
        color: {theme.text_primary};
        border: 1px solid {theme.border};
        border-radius: 6px;
    }}
    QLineEdit:focus, QPlainTextEdit:focus {{
        border-color: {theme.input_focus};
    }}
    QToolButton#setupDisclosure {{
        color: {theme.text_secondary};
        background: transparent;
        border: none;
        padding: 5px 0;
        font-weight: 600;
    }}
    QPushButton {{
        min-height: 36px;
        padding: 0 16px;
        border-radius: 6px;
        font-weight: 700;
    }}
    QPushButton#setupPrimaryButton {{
        background: {theme.accent};
        color: {theme.accent_foreground};
        border: 1px solid {theme.accent};
    }}
    QPushButton#setupPrimaryButton:hover {{ background: {theme.accent_hover}; }}
    QPushButton#setupPrimaryButton:pressed {{ background: {theme.accent_pressed}; }}
    QPushButton#setupSecondaryButton, QPushButton#setupTertiaryButton {{
        background: {theme.surface};
        color: {theme.text_primary};
        border: 1px solid {theme.border};
    }}
    QPushButton#setupTertiaryButton {{ min-height: 30px; padding: 0 12px; }}
    QPushButton:disabled {{ color: {theme.text_muted}; }}
    QProgressBar {{
        background: {theme.border_soft};
        border: none;
        border-radius: 3px;
    }}
    QProgressBar::chunk {{
        background: {theme.accent};
        border-radius: 3px;
    }}
    QScrollArea#setupDeviceScroll {{ background: transparent; }}
    """
