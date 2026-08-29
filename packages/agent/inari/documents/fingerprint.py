from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any

import rfc8785

from .models import DocumentKind


OptionsCanonicalizer = Callable[[Mapping[str, Any]], bytes]

_FINGERPRINT_FORMAT = b"inari-device-work-fingerprint\x00\x01"
_MAX_FIELD_LENGTH = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class DeviceWorkFingerprintInput:
    """The stable fields that identify one Device Work request.

    Request metadata is deliberately absent. In particular, an idempotency key
    identifies the replay record, while this value identifies the work stored
    in that record.
    """

    contract_major: int
    operation: DocumentKind | str
    device_id: str
    media_type: str
    document: bytes
    options: Mapping[str, Any]
    expires_at: datetime


def _canonicalize_options(options: Mapping[str, Any]) -> bytes:
    """Canonicalize JSON options with RFC 8785.

    Copy the outer mapping so callers can pass another read-only Mapping
    implementation without changing the canonical JSON contract.
    """

    return rfc8785.dumps(dict(options))


def fingerprint_device_work(
    work: DeviceWorkFingerprintInput,
    *,
    canonicalize_options: OptionsCanonicalizer = _canonicalize_options,
) -> bytes:
    """Return the SHA-256 fingerprint for one normalized Device Work item.

    The representation has a fixed field order and an unsigned 64-bit byte
    length before each field. This keeps binary payloads unambiguous and lets
    the format evolve through its explicit format version.
    """

    _validate_work(work)
    options = canonicalize_options(work.options)
    if not isinstance(options, bytes):
        raise TypeError("The options canonicalizer must return bytes.")

    fields = (
        work.contract_major.to_bytes(4, byteorder="big", signed=False),
        DocumentKind(work.operation).value.encode("utf-8"),
        work.device_id.encode("utf-8"),
        work.media_type.strip().lower().encode("ascii"),
        work.document,
        options,
        _format_deadline(work.expires_at),
    )
    representation = _FINGERPRINT_FORMAT + b"".join(
        _length_prefix(field) for field in fields
    )
    return hashlib.sha256(representation).digest()


def _validate_work(work: DeviceWorkFingerprintInput) -> None:
    if not isinstance(work.contract_major, int) or isinstance(
        work.contract_major, bool
    ):
        raise TypeError("contract_major must be an integer.")
    if not 0 <= work.contract_major <= (1 << 32) - 1:
        raise ValueError("contract_major must fit in an unsigned 32-bit integer.")

    if not isinstance(work.operation, (DocumentKind, str)):
        raise TypeError("operation must be a supported DocumentKind.")
    try:
        operation = DocumentKind(work.operation).value
    except ValueError as exc:
        raise ValueError("operation must be a supported DocumentKind.") from exc
    if not operation:
        raise ValueError("operation must not be empty.")

    if not isinstance(work.device_id, str):
        raise TypeError("device_id must be a string.")
    if not work.device_id:
        raise ValueError("device_id must not be empty.")

    if not isinstance(work.media_type, str):
        raise TypeError("media_type must be a string.")
    media_type = work.media_type.strip().lower()
    if not _MEDIA_TYPE_PATTERN.fullmatch(media_type):
        raise ValueError("media_type must be a normalized type/subtype.")

    if not isinstance(work.document, bytes):
        raise TypeError("document must be bytes.")
    if not isinstance(work.options, Mapping):
        raise TypeError("options must be a mapping.")
    if not isinstance(work.expires_at, datetime):
        raise TypeError("expires_at must be a datetime.")
    if work.expires_at.tzinfo is None or work.expires_at.utcoffset() is None:
        raise ValueError("expires_at must be timezone-aware.")


_MEDIA_TYPE_PATTERN = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+\Z")


def _format_deadline(value: datetime) -> bytes:
    normalized = value.astimezone(UTC)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ").encode("ascii")


def _length_prefix(value: bytes) -> bytes:
    if len(value) > _MAX_FIELD_LENGTH:
        raise ValueError("Fingerprint field is too large.")
    return len(value).to_bytes(8, byteorder="big") + value
