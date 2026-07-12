from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import DeviceDriver, DeviceKind, DriverMetadata
    from .registry import DriverRegistry

__all__ = [
    "DeviceDriver",
    "DeviceKind",
    "DriverMetadata",
    "DriverRegistry",
]


def __getattr__(name: str) -> Any:
    if name in {"DeviceDriver", "DeviceKind", "DriverMetadata"}:
        from . import base

        value = getattr(base, name)
    elif name == "DriverRegistry":
        from .registry import DriverRegistry

        value = DriverRegistry
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
