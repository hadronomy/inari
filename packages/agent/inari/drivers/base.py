from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable


class DeviceKind(StrEnum):
    PRINTER = "printer"
    SCANNER = "scanner"
    SCALE = "scale"
    DISPLAY = "display"


class DeviceTransport(StrEnum):
    SPOOLER = "spooler"
    NETWORK = "network"
    USB = "usb"
    HID = "hid"
    SERIAL = "serial"


@dataclass(slots=True, frozen=True)
class DeviceIdentity:
    transport: DeviceTransport
    serial_number: str | None = None
    vendor_id: int | None = None
    product_id: int | None = None
    os_instance_id: str | None = None
    port_id: str | None = None

    def stable_key(self) -> str:
        if self.serial_number:
            vendor = _hex_identifier(self.vendor_id)
            product = _hex_identifier(self.product_id)
            return f"hardware:{vendor}:{product}:{self.serial_number}"
        if self.os_instance_id:
            return f"os:{self.transport.value}:{self.os_instance_id}"
        if self.port_id:
            return f"port:{self.transport.value}:{self.port_id}"
        raise ValueError(
            "Device identity requires a hardware serial, operating-system "
            "instance identifier, or stable port identifier."
        )


def _hex_identifier(value: int | None) -> str:
    return f"{value:04x}" if value is not None else "unknown"


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
