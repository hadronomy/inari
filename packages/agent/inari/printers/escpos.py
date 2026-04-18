from __future__ import annotations

from .types import CutMode
from ..exceptions import PrinterServiceError


class EscPosCommands:
    INITIALIZE = b"\x1b@"
    DRAWER_PULSE = b"\x1b\x70\x00\x19\xfa"

    @staticmethod
    def feed_lines(count: int) -> bytes:
        if count < 1:
            raise PrinterServiceError(
                "INVALID_FEED", "Line feed count must be at least 1."
            )

        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = min(remaining, 255)
            chunks.append(b"\x1b\x64" + bytes((chunk,)))
            remaining -= chunk
        return b"".join(chunks)

    @staticmethod
    def feed_dots(count: int) -> bytes:
        if count < 1:
            raise PrinterServiceError(
                "INVALID_FEED", "Dot feed count must be at least 1."
            )

        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = min(remaining, 255)
            chunks.append(b"\x1b\x4a" + bytes((chunk,)))
            remaining -= chunk
        return b"".join(chunks)

    @staticmethod
    def cut(mode: CutMode) -> bytes:
        if mode is CutMode.FULL:
            return b"\x1d\x56\x00"
        return b"\x1d\x56\x01"
