from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .commands import (
        AnyDeviceCommand,
        CutPaper,
        DeviceCommand,
        DeviceCommandKind,
        FeedDots,
        FeedLines,
        OpenCashDrawer,
        PrintTestPage,
    )
    from .service import PrinterService

__all__ = [
    "AnyDeviceCommand",
    "CutPaper",
    "DeviceCommand",
    "DeviceCommandKind",
    "FeedDots",
    "FeedLines",
    "OpenCashDrawer",
    "PrintTestPage",
    "PrinterService",
]

_COMMAND_EXPORTS = {
    "AnyDeviceCommand",
    "CutPaper",
    "DeviceCommand",
    "DeviceCommandKind",
    "FeedDots",
    "FeedLines",
    "OpenCashDrawer",
    "PrintTestPage",
}

def __getattr__(name: str) -> Any:
    if name in _COMMAND_EXPORTS:
        from . import commands

        value = getattr(commands, name)
    elif name == "PrinterService":
        from .service import PrinterService

        value = PrinterService
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
