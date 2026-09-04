from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

from .models import DocumentAdmissionError

_PDF_MAX_BYTES = 10 * 1024 * 1024
_LABEL_MAX_BYTES = 2 * 1024 * 1024
_ZPL_COMMAND = re.compile(r"([\^~])([A-Z0-9@]{2})")
_ZPL_ALLOWED_COMMANDS = frozenset(
    {
        "A0",
        "B3",
        "BC",
        "BQ",
        "BY",
        "CF",
        "CI",
        "FB",
        "FD",
        "FO",
        "FR",
        "FS",
        "FT",
        "FW",
        "GB",
        "GC",
        "LH",
        "LL",
        "LS",
        "LT",
        "PO",
        "PW",
        "XA",
        "XZ",
    }
)


class QpdfDocumentValidator:
    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        self._runner = runner

    def validate(self, content: bytes) -> None:
        _validate_bytes(content, label="Report PDF", limit=_PDF_MAX_BYTES)
        if not content.startswith(b"%PDF-"):
            raise DocumentAdmissionError(
                "payload_invalid", "Report PDF content is not a PDF document."
            )
        try:
            result = self._runner(
                ["qpdf", "--check", "-"],
                input=content,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
        except FileNotFoundError as exc:
            raise DocumentAdmissionError(
                "service_unavailable",
                "The isolated PDF validator is not installed.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DocumentAdmissionError(
                "service_unavailable",
                "The isolated PDF validator did not finish in time.",
            ) from exc
        except OSError as exc:
            raise DocumentAdmissionError(
                "service_unavailable",
                "The isolated PDF validator could not start.",
            ) from exc
        if result.returncode != 0:
            raise DocumentAdmissionError(
                "payload_invalid", "Report PDF content failed structural validation."
            )


def validate_label_document(content: bytes) -> None:
    _validate_bytes(content, label="Label document", limit=_LABEL_MAX_BYTES)
    try:
        source = content.decode("ascii")
    except UnicodeDecodeError as exc:
        raise DocumentAdmissionError(
            "payload_invalid", "Label document content must use ASCII ZPL."
        ) from exc
    if any(character < " " and character not in "\r\n\t" for character in source):
        raise DocumentAdmissionError(
            "payload_invalid", "Label document content has invalid control bytes."
        )

    matches = tuple(_ZPL_COMMAND.finditer(source))
    if not matches or source[: matches[0].start()].strip():
        raise DocumentAdmissionError(
            "payload_invalid", "Label document content must start with ^XA."
        )
    if len(matches) > 4096:
        raise DocumentAdmissionError(
            "payload_invalid", "Label document contains too many ZPL commands."
        )

    in_format = False
    field_open = False
    field_count = 0
    for index, match in enumerate(matches):
        prefix, command = match.groups()
        next_start = (
            matches[index + 1].start() if index + 1 < len(matches) else len(source)
        )
        arguments = source[match.end() : next_start]
        if prefix != "^" or command not in _ZPL_ALLOWED_COMMANDS:
            raise DocumentAdmissionError(
                "document_policy_rejected",
                f"Label document command {prefix}{command} is not permitted.",
            )
        if command == "XA":
            if in_format or arguments.strip():
                raise DocumentAdmissionError(
                    "payload_invalid", "Label document has an invalid ^XA boundary."
                )
            in_format = True
            field_open = False
            continue
        if command == "XZ":
            if not in_format or field_open or arguments.strip():
                raise DocumentAdmissionError(
                    "payload_invalid", "Label document has an invalid ^XZ boundary."
                )
            in_format = False
            continue
        if not in_format:
            raise DocumentAdmissionError(
                "payload_invalid", "ZPL commands must be inside ^XA and ^XZ."
            )
        if command == "FD":
            if field_open:
                raise DocumentAdmissionError(
                    "payload_invalid", "Label document has nested field data."
                )
            field_open = True
            field_count += 1
        elif command == "FS":
            if not field_open or arguments.strip():
                raise DocumentAdmissionError(
                    "payload_invalid", "Label document has an unmatched ^FS command."
                )
            field_open = False
        elif field_open:
            raise DocumentAdmissionError(
                "payload_invalid", "Label field data must end with ^FS."
            )

    if in_format or field_open or field_count == 0:
        raise DocumentAdmissionError(
            "payload_invalid", "Label document has an incomplete ZPL format."
        )
    if source[matches[-1].end() :].strip():
        raise DocumentAdmissionError(
            "payload_invalid", "Label document has data after its final command."
        )


def _validate_bytes(content: bytes, *, label: str, limit: int) -> None:
    if not isinstance(content, bytes) or not content:
        raise DocumentAdmissionError(
            "payload_invalid", f"{label} content must be non-empty bytes."
        )
    if len(content) > limit:
        raise DocumentAdmissionError(
            "payload_invalid", f"{label} exceeds its {limit}-byte limit."
        )


__all__ = ["QpdfDocumentValidator", "validate_label_document"]
