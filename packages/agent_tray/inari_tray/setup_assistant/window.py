from __future__ import annotations

from collections.abc import Callable

from inari.local_api.schemas import (
    DeviceResponse,
    ManagedOnboardingStatusResponse,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
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
from .presenter import SetupBridge, SetupClient, SetupPresenter
from .state import SetupStep
from .style import setup_style_sheet


class SetupAssistantWindow(QMainWindow):
    def __init__(
        self,
        settings: TraySettings,
        *,
        client: SetupClient,
        bridge: SetupBridge,
        on_ready: Callable[[ManagedOnboardingStatusResponse], None] | None = None,
    ) -> None:
        super().__init__()
        self._retry_action: Callable[[], None] | None = None
        self._device_rows: list[
            tuple[DeviceResponse, QCheckBox, QLineEdit, QRadioButton]
        ] = []
        self._default_group = QButtonGroup(self)
        self._default_group.setExclusive(True)
        self._build_ui()
        self.presenter = SetupPresenter(
            client=client,
            bridge=bridge,
            view=self,
            on_complete=on_ready,
        )

    def show_with_invitation(self, invitation: str | None = None) -> None:
        self.presenter.present_invitation(invitation)
        self.show()
        self.raise_()
        self.activateWindow()

    def resume(self, status: ManagedOnboardingStatusResponse) -> None:
        self.presenter.resume(status)
        self.show()
        self.raise_()
        self.activateWindow()

    def show_unavailable(self, message: str, retry: Callable[[], None]) -> None:
        self.presenter.unavailable(message, retry)
        self.show()
        self.raise_()
        self.activateWindow()

    def _build_ui(self) -> None:
        self.setWindowTitle("Connect to Inari")
        self.setMinimumSize(760, 540)
        self.resize(820, 580)
        self.setObjectName("setupAssistantWindow")
        theme = resolve_device_center_theme()
        self.setStyleSheet(setup_style_sheet(theme))

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
        self.connect_button.clicked.connect(self._submit_invitation)
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
        self.cancel_button.clicked.connect(lambda: self.presenter.cancel())
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        self.retry_button = QPushButton("Try Again")
        self.retry_button.setObjectName("setupPrimaryButton")
        self.retry_button.clicked.connect(self._retry)
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
        self.device_empty = QLabel(
            "No devices are attached yet. You can finish setup and add them later."
        )
        self.device_empty.setObjectName("setupDeviceEmpty")
        self.device_empty.setWordWrap(True)
        self.device_empty.setVisible(False)
        layout.addWidget(self.device_empty)
        actions = QHBoxLayout()
        self.rescan_button = QPushButton("Refresh")
        self.rescan_button.setObjectName("setupSecondaryButton")
        self.rescan_button.clicked.connect(lambda: self.presenter.check_status())
        actions.addWidget(self.rescan_button)
        actions.addStretch()
        self.confirm_button = QPushButton("Continue")
        self.confirm_button.setObjectName("setupPrimaryButton")
        self.confirm_button.clicked.connect(self._submit_devices)
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
        finish.clicked.connect(lambda: self.presenter.finish())
        actions.addWidget(finish)
        layout.addLayout(actions)
        return page

    def _submit_invitation(self) -> None:
        invitation = self.invitation_input.toPlainText().strip()
        if not invitation:
            self.show_invitation_page(error="Enter your invitation link or code.")
            return
        server = self.server_field.text().strip() or None
        self.presenter.begin(invitation, server)

    def _submit_devices(self) -> None:
        selected_devices = [
            (device, label, default)
            for device, selected, label, default in self._device_rows
            if selected.isChecked()
        ]
        device_ids = tuple(device.id for device, _, _ in selected_devices)
        labels = {
            device.id: label.text().strip()
            for device, label, _ in selected_devices
            if label.text().strip()
        }
        default_device_id = next(
            (
                device.id
                for device, _, default in selected_devices
                if default.isChecked()
            ),
            None,
        )
        self.presenter.confirm_devices(
            device_ids=device_ids,
            labels=labels,
            default_printer_device_id=default_device_id,
        )

    def _retry(self) -> None:
        if self._retry_action is not None:
            self._retry_action()

    def show_invitation_page(
        self, *, invitation: str | None = None, error: str | None = None
    ) -> None:
        self.pages.setCurrentIndex(0)
        self._set_step(SetupStep.INVITATION)
        if invitation is not None:
            self.invitation_input.setPlainText(invitation)
        self.connect_button.setEnabled(True)
        self.invitation_error.setText(error or "")
        self.invitation_error.setVisible(bool(error))

    def show_connection_page(
        self,
        *,
        step: SetupStep,
        message: str,
        error: str | None = None,
        retry: Callable[[], None] | None = None,
        allow_start_over: bool = False,
    ) -> None:
        self.pages.setCurrentIndex(1)
        self._set_step(step)
        self.connection_status.setText(message)
        self.connection_error.setText(error or "")
        self.connection_error.setVisible(bool(error))
        self.connection_progress.setVisible(error is None)
        self.cancel_button.setEnabled(True)
        if allow_start_over:
            self._retry_action = self.presenter.start_over
            self.retry_button.setText("Start over")
        else:
            self._retry_action = retry
            self.retry_button.setText("Try again")
        self.retry_button.setVisible(self._retry_action is not None)

    def show_devices_page(self, devices: list[DeviceResponse]) -> None:
        self._populate_devices(devices)
        self.pages.setCurrentIndex(2)
        self._set_step(SetupStep.DEVICES)
        self.device_empty.setVisible(not devices)
        self.confirm_button.setText(
            "Continue without devices" if not devices else "Continue"
        )
        self.confirm_button.setEnabled(True)
        self.rescan_button.setEnabled(True)

    def show_ready_page(self, status: ManagedOnboardingStatusResponse) -> None:
        self._set_step(SetupStep.READY)
        count = len(status.devices)
        self.ready_summary.setText(
            f"Connected {count} device{'s' if count != 1 else ''}."
            if count
            else "Connected. You can add devices at any time."
        )
        self.pages.setCurrentIndex(3)

    def update_details(self, value: object) -> None:
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

    def set_confirm_busy(self, busy: bool) -> None:
        self.confirm_button.setEnabled(not busy)
        self.rescan_button.setEnabled(not busy)

    def set_cancel_busy(self, busy: bool) -> None:
        self.cancel_button.setEnabled(not busy)

    def dismiss_view(self) -> None:
        self.close()

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
                    lambda checked=False, device_id=device.id: (
                        self.presenter.test_device(device_id)
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

    def shutdown(self) -> None:
        self.presenter.shutdown()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.presenter.suspend()
        super().closeEvent(event)


def create_setup_assistant(
    settings: TraySettings,
    *,
    client: SetupClient,
    bridge: SetupBridge,
    on_ready: Callable[[ManagedOnboardingStatusResponse], None] | None = None,
) -> SetupAssistantWindow:
    return SetupAssistantWindow(
        settings,
        client=client,
        bridge=bridge,
        on_ready=on_ready,
    )
