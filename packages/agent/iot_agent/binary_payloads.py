from __future__ import annotations

import binascii
from base64 import b64decode

from .exceptions import PrinterServiceError


def decode_base64_payload(value: str, *, label: str = "payload") -> bytes:
    normalized = value.strip()
    if normalized.startswith("data:") and "," in normalized:
        _, normalized = normalized.split(",", 1)

    try:
        return b64decode(normalized, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PrinterServiceError("INVALID_BASE64", f"Invalid base64 {label}.") from exc
