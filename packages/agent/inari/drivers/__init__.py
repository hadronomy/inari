from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import (
        DeviceDriver,
        DeviceIdentity,
        DeviceKind,
        DeviceTransport,
        DriverMetadata,
    )
    from .registry import DriverRegistry

__all__ = [
    "DeviceDriver",
    "DeviceIdentity",
    "DeviceKind",
    "DeviceTransport",
    "DriverMetadata",
    "DriverRegistry",
]


def __getattr__(name: str) -> Any:
    if name in {
        "DeviceDriver",
        "DeviceIdentity",
        "DeviceKind",
        "DeviceTransport",
        "DriverMetadata",
    }:
        from . import base

        value = getattr(base, name)
    elif name == "DriverRegistry":
        from .registry import DriverRegistry

        value = DriverRegistry
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
