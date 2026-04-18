from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from .theme import DeviceCenterTheme


class BusySpinner(QWidget):
    def __init__(self, theme: DeviceCenterTheme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._angle = 0
        self._mode = "busy"
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(14, 14)

    def start(self) -> None:
        self._mode = "busy"
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def show_ready(self) -> None:
        self._mode = "ready"
        self.stop()

    def show_offline(self) -> None:
        self._mode = "offline"
        self.stop()

    def stop(self) -> None:
        self._timer.stop()
        self._angle = 0
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(2, 2, -2, -2)

        if self._mode != "busy":
            color = (
                self._theme.color(self._theme.success_foreground)
                if self._mode == "ready"
                else self._theme.color(self._theme.offline_foreground)
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect.center(), 4, 4)
            return

        track = self._theme.color(self._theme.border)
        track.setAlpha(120)
        painter.setPen(QPen(track, 2))
        painter.drawArc(rect, 0, 360 * 16)

        pen = QPen(self._theme.color(self._theme.accent), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, self._angle * 16, 110 * 16)

    def _tick(self) -> None:
        self._angle = (self._angle - 28) % 360
        self.update()


class ActivityOverlay(QFrame):
    def __init__(self, theme: DeviceCenterTheme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("activityOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self._spinner = BusySpinner(theme, self)
        layout.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._message = QLabel("Connected", self)
        self._message.setObjectName("activityMessage")
        layout.addWidget(self._message)
        layout.addStretch(1)

    def set_status(self, mode: str, message: str) -> None:
        self.setProperty("statusMode", mode)
        self.style().unpolish(self)
        self.style().polish(self)
        if mode == "busy":
            self._spinner.start()
        elif mode == "offline":
            self._spinner.show_offline()
        else:
            self._spinner.show_ready()
        self._message.setText(message)
        self.adjustSize()
