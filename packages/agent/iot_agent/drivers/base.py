from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable


class DeviceKind(StrEnum):
    PRINTER = "printer"
    SCANNER = "scanner"
    SCALE = "scale"
    DISPLAY = "display"


@dataclass(slots=True, frozen=True)
class DriverMetadata:
    key: str
    display_name: str
    kind: DeviceKind
    platform: str


@runtime_checkable
class DeviceDriver(Protocol):
    metadata: ClassVar[DriverMetadata]

    def is_available(self) -> bool:
        """Return whether the driver can operate on the current machine."""
