from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from inari.models import RuntimeEventResponse

from ..config import TraySettings
from ..models import TraySnapshot
from .contracts import DeviceCenterClient
from .controller import QtDeviceCenterController


class DeviceCenterPresenter(Protocol):
    def show(self) -> None: ...

    def update_connection_snapshot(self, snapshot: TraySnapshot) -> None: ...

    def handle_runtime_event(self, event: RuntimeEventResponse) -> None: ...

    def close(self) -> None: ...


def create_device_center(
    settings: TraySettings,
    *,
    client: DeviceCenterClient,
    notify: Callable[[str, str | None], None] | None = None,
) -> DeviceCenterPresenter:
    return QtDeviceCenterController(settings=settings, client=client, notify=notify)
