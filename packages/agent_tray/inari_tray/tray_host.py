from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .config import TraySettings
from .models import TraySnapshot

MenuCallback = Callable[[], None]


@dataclass(slots=True, frozen=True)
class TrayMenuEntry:
    label: str = ""
    callback: MenuCallback | None = None
    enabled: bool = True
    visible: bool = True
    default: bool = False
    separator: bool = False

    @classmethod
    def separator_item(cls) -> TrayMenuEntry:
        return cls(separator=True)


class TrayHost(Protocol):
    def run(
        self,
        *,
        snapshot: TraySnapshot,
        menu_entries: Sequence[TrayMenuEntry],
        on_ready: Callable[[], None],
    ) -> None: ...

    def update(
        self, *, snapshot: TraySnapshot, menu_entries: Sequence[TrayMenuEntry]
    ) -> None: ...

    def notify(self, *, title: str, message: str) -> None: ...

    def stop(self) -> None: ...


def create_tray_host(settings: TraySettings) -> TrayHost:
    from .qt_host import QtTrayHost

    return QtTrayHost(title=settings.title)
