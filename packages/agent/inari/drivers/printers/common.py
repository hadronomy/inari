from __future__ import annotations

from ...printers import PrinterTransport

RECEIPT_RAW_NAME_HINTS = frozenset(
    {
        "epson tm",
        "tm-t",
        "receipt",
        "pos",
        "esc/pos",
        "thermal",
        "star tsp",
        "bixolon",
    }
)


def guess_preferred_transport(
    printer_name: str,
    *,
    raw_name_hints: frozenset[str] = RECEIPT_RAW_NAME_HINTS,
    device_uri: str | None = None,
) -> PrinterTransport:
    normalized_name = printer_name.casefold()
    normalized_uri = (device_uri or "").casefold()
    if any(hint in normalized_name for hint in raw_name_hints):
        return PrinterTransport.RAW
    if normalized_uri.startswith("socket://") or normalized_uri.startswith("tcp://"):
        return PrinterTransport.RAW
    return PrinterTransport.DOCUMENT
