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
    from .jobs import (
        HtmlDocumentContent,
        PdfDocumentContent,
        PrintContent,
        PrintContentKind,
        PrintJob,
        RawDocumentContent,
        ReceiptImageContent,
        StructuredReceiptContent,
        TextDocumentContent,
    )
    from .payloads import BinaryPayload, DetectedMediaType
    from .service import PrinterService

__all__ = [
    "AnyDeviceCommand",
    "BinaryPayload",
    "CutPaper",
    "DetectedMediaType",
    "DeviceCommand",
    "DeviceCommandKind",
    "FeedDots",
    "FeedLines",
    "HtmlDocumentContent",
    "OpenCashDrawer",
    "PdfDocumentContent",
    "PrintContent",
    "PrintContentKind",
    "PrintJob",
    "PrintTestPage",
    "PrinterService",
    "RawDocumentContent",
    "ReceiptImageContent",
    "StructuredReceiptContent",
    "TextDocumentContent",
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
_JOB_EXPORTS = {
    "HtmlDocumentContent",
    "PdfDocumentContent",
    "PrintContent",
    "PrintContentKind",
    "PrintJob",
    "RawDocumentContent",
    "ReceiptImageContent",
    "StructuredReceiptContent",
    "TextDocumentContent",
}
_PAYLOAD_EXPORTS = {"BinaryPayload", "DetectedMediaType"}


def __getattr__(name: str) -> Any:
    if name in _COMMAND_EXPORTS:
        from . import commands

        value = getattr(commands, name)
    elif name in _JOB_EXPORTS:
        from . import jobs

        value = getattr(jobs, name)
    elif name in _PAYLOAD_EXPORTS:
        from . import payloads

        value = getattr(payloads, name)
    elif name == "PrinterService":
        from .service import PrinterService

        value = PrinterService
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
