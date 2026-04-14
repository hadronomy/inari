from __future__ import annotations

from io import BytesIO
import logging
from typing import Callable, Sequence

from PIL.Image import Image
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .icons import build_tray_icon
from .models import TraySnapshot
from .tray_host import TrayHost, TrayMenuEntry

logger = logging.getLogger(__name__)


class _TraySignals(QObject):
    update_requested = Signal(object, object)
    notify_requested = Signal(str, str)
    stop_requested = Signal()


class QtTrayHost(TrayHost):
    def __init__(self, *, title: str) -> None:
        self._title = title
        self._application: QApplication | None = None
        self._tray_icon: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None
        self._signals: _TraySignals | None = None

    def run(
        self,
        *,
        snapshot: TraySnapshot,
        menu_entries: Sequence[TrayMenuEntry],
        on_ready: Callable[[], None],
    ) -> None:
        application = QApplication.instance()
        if application is None:
            application = QApplication([self._title])
        application.setQuitOnLastWindowClosed(False)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError("No system tray is available in this desktop session.")

        self._application = application
        self._signals = _TraySignals()
        self._signals.update_requested.connect(self._apply_update)
        self._signals.notify_requested.connect(self._show_message)
        self._signals.stop_requested.connect(self._stop)

        tray_icon = QSystemTrayIcon()
        tray_icon.setVisible(True)
        self._tray_icon = tray_icon
        self._apply_update(snapshot, list(menu_entries))
        tray_icon.show()
        QTimer.singleShot(0, lambda: self._run_ready_callback(on_ready))
        application.exec()

    def update(self, *, snapshot: TraySnapshot, menu_entries: Sequence[TrayMenuEntry]) -> None:
        if self._signals is None:
            return
        self._signals.update_requested.emit(snapshot, list(menu_entries))

    def notify(self, *, title: str, message: str) -> None:
        if self._signals is None:
            return
        self._signals.notify_requested.emit(title, message)

    def stop(self) -> None:
        if self._signals is None:
            return
        self._signals.stop_requested.emit()

    def _apply_update(self, snapshot: TraySnapshot, menu_entries: Sequence[TrayMenuEntry]) -> None:
        if self._tray_icon is None:
            return
        self._tray_icon.setIcon(_image_to_qicon(build_tray_icon(snapshot)))
        self._tray_icon.setToolTip(snapshot.tooltip)
        menu = _build_menu(menu_entries)
        self._menu = menu
        self._tray_icon.setContextMenu(menu)

    def _show_message(self, title: str, message: str) -> None:
        if self._tray_icon is None or not self._tray_icon.supportsMessages():
            return
        self._tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 5000)

    def _run_ready_callback(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            logger.exception("Tray background setup failed")

    def _stop(self) -> None:
        if self._tray_icon is not None:
            self._tray_icon.hide()
        application = self._application
        if application is None:
            return
        application.quit()


def _build_menu(menu_entries: Sequence[TrayMenuEntry]) -> QMenu:
    menu = QMenu()
    default_action: QAction | None = None
    for entry in menu_entries:
        if entry.separator:
            menu.addSeparator()
            continue
        action = QAction(entry.label, menu)
        action.setEnabled(entry.enabled)
        if entry.callback is not None:
            action.triggered.connect(lambda checked=False, callback=entry.callback: callback())
        menu.addAction(action)
        if entry.default and default_action is None:
            default_action = action
    if default_action is not None:
        menu.setDefaultAction(default_action)
    return menu


def _image_to_qicon(image: Image) -> QIcon:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    qimage = QImage.fromData(buffer.getvalue(), "PNG")
    pixmap = QPixmap.fromImage(qimage)
    return QIcon(pixmap)
