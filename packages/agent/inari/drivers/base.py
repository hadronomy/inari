from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import ClassVar, Protocol, runtime_checkable
import unicodedata


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

    def __post_init__(self) -> None:
        try:
            transport = DeviceTransport(self.transport)
        except ValueError as exc:
            raise ValueError("Device identity transport is not supported.") from exc
        object.__setattr__(self, "transport", transport)

        for field_name in ("serial_number", "os_instance_id", "port_id"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise TypeError(f"Device identity {field_name} must be a string.")
            normalized = unicodedata.normalize("NFC", value).strip()
            object.__setattr__(self, field_name, normalized or None)

        for field_name in ("vendor_id", "product_id"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"Device identity {field_name} must be an integer.")
            if not 0 <= value <= 0xFFFF:
                raise ValueError(
                    f"Device identity {field_name} must be an unsigned 16-bit value."
                )

    def stable_key(self) -> str:
        """Return the versioned identity basis.

        Hardware identity has priority because it stays stable across transports.
        An OS instance has priority over a configured port when no serial exists.
        """

        if self.serial_number:
            identity = {
                "kind": "hardware",
                "product_id": self.product_id,
                "serial_number": self.serial_number,
                "vendor_id": self.vendor_id,
            }
        elif self.os_instance_id:
            identity = {
                "kind": "os",
                "instance_id": self.os_instance_id,
                "transport": self.transport.value,
            }
        elif self.port_id:
            identity = {
                "kind": "port",
                "port_id": self.port_id,
                "transport": self.transport.value,
            }
        else:
            raise ValueError(
                "Device identity requires a hardware serial, operating-system "
                "instance identifier, or stable port identifier."
            )
        return "device-identity-v1:" + json.dumps(
            identity,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


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
