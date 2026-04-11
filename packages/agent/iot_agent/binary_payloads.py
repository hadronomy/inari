from __future__ import annotations

import binascii
import logging
from base64 import b64decode
from dataclasses import dataclass, replace
from typing import Literal

import puremagic

from .exceptions import PrinterServiceError

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DetectedMediaType:
    mime_type: str | None = None
    extension: str | None = None
    description: str | None = None
    confidence: float | None = None
    detector: Literal["puremagic"] = "puremagic"


@dataclass(slots=True, frozen=True)
class BinaryPayload:
    content: bytes
    source: Literal["base64", "data_url"] = "base64"
    declared_mime_types: tuple[str, ...] = ()
    detected_type: DetectedMediaType | None = None

    @property
    def mime_type(self) -> str | None:
        if self.detected_type and self.detected_type.mime_type:
            return self.detected_type.mime_type
        if self.declared_mime_types:
            return self.declared_mime_types[0]
        return None

    def with_declared_mime_type(self, mime_type: str | None, *, label: str = "payload") -> BinaryPayload:
        normalized = normalize_mime_type(mime_type)
        if normalized is None:
            return self

        if self.declared_mime_types and normalized not in self.declared_mime_types:
            raise PrinterServiceError(
                "MIME_TYPE_MISMATCH",
                f"Conflicting MIME type declarations for {label}.",
            )

        if normalized in self.declared_mime_types:
            return self

        return replace(self, declared_mime_types=self.declared_mime_types + (normalized,))


def decode_base64_payload(value: str, *, label: str = "payload") -> BinaryPayload:
    normalized = value.strip()
    source: Literal["base64", "data_url"] = "base64"
    declared_mime_types: tuple[str, ...] = ()

    if normalized.startswith("data:"):
        source = "data_url"
        declared_mime_type, normalized = _parse_data_url(normalized, label=label)
        if declared_mime_type is not None:
            declared_mime_types = (declared_mime_type,)

    try:
        content = b64decode(normalized, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PrinterServiceError("INVALID_BASE64", f"Invalid base64 {label}.") from exc

    return BinaryPayload(
        content=content,
        source=source,
        declared_mime_types=declared_mime_types,
        detected_type=detect_media_type(content),
    )


def coerce_image_payload(
    value: str,
    *,
    label: str,
    declared_mime_type: str | None = None,
) -> BinaryPayload:
    payload = decode_base64_payload(value, label=label).with_declared_mime_type(declared_mime_type, label=label)
    return validate_binary_payload(
        payload,
        label=label,
        allowed_mime_prefixes=("image/",),
        allow_family_match=True,
    )


def coerce_pdf_payload(
    value: str,
    *,
    label: str,
    declared_mime_type: str | None = None,
) -> BinaryPayload:
    payload = decode_base64_payload(value, label=label).with_declared_mime_type(declared_mime_type, label=label)
    return validate_binary_payload(
        payload,
        label=label,
        allowed_mime_types=("application/pdf",),
        allow_family_match=False,
    )


def coerce_raw_payload(
    value: str,
    *,
    label: str,
    declared_mime_type: str | None = None,
) -> BinaryPayload:
    return decode_base64_payload(value, label=label).with_declared_mime_type(declared_mime_type, label=label)


def validate_binary_payload(
    payload: BinaryPayload,
    *,
    label: str,
    allowed_mime_types: tuple[str, ...] = (),
    allowed_mime_prefixes: tuple[str, ...] = (),
    allow_family_match: bool,
) -> BinaryPayload:
    for mime_type in payload.declared_mime_types:
        if not _mime_allowed(mime_type, allowed_mime_types=allowed_mime_types, allowed_mime_prefixes=allowed_mime_prefixes):
            raise PrinterServiceError(
                "INVALID_DECLARED_MIME_TYPE",
                f"Declared MIME type {mime_type!r} is not valid for {label}.",
            )

    detected_mime_type = payload.detected_type.mime_type if payload.detected_type else None
    if detected_mime_type and not _mime_allowed(
        detected_mime_type,
        allowed_mime_types=allowed_mime_types,
        allowed_mime_prefixes=allowed_mime_prefixes,
    ):
        raise PrinterServiceError(
            "INVALID_DETECTED_MIME_TYPE",
            f"Detected MIME type {detected_mime_type!r} is not valid for {label}.",
        )

    if detected_mime_type and payload.declared_mime_types:
        for declared_mime_type in payload.declared_mime_types:
            if not _mime_compatible(
                declared_mime_type,
                detected_mime_type,
                allow_family_match=allow_family_match,
            ):
                raise PrinterServiceError(
                    "MIME_TYPE_MISMATCH",
                    f"Declared MIME type {declared_mime_type!r} does not match detected MIME type {detected_mime_type!r} for {label}.",
                )

    if detected_mime_type is None and not payload.declared_mime_types:
        raise PrinterServiceError(
            "MIME_TYPE_UNKNOWN",
            f"Could not determine the MIME type for {label}.",
        )

    return payload


def detect_media_type(content: bytes) -> DetectedMediaType | None:
    try:
        matches = puremagic.magic_string(content)
    except Exception:  # pragma: no cover - detector failures should not crash request parsing
        logger.debug("puremagic could not identify payload", exc_info=True)
        return None

    if not matches:
        return None

    match = matches[0]
    return DetectedMediaType(
        mime_type=normalize_mime_type(getattr(match, "mime_type", None)),
        extension=_normalize_optional_text(getattr(match, "extension", None)),
        description=_normalize_optional_text(getattr(match, "name", None)),
        confidence=_normalize_confidence(getattr(match, "confidence", None)),
    )


def normalize_mime_type(mime_type: str | None) -> str | None:
    if mime_type is None:
        return None

    normalized = mime_type.strip().lower()
    if not normalized:
        return None

    aliases = {
        "image/jpg": "image/jpeg",
        "application/x-pdf": "application/pdf",
    }
    return aliases.get(normalized, normalized)


def _parse_data_url(value: str, *, label: str) -> tuple[str | None, str]:
    try:
        header, encoded = value.split(",", 1)
    except ValueError as exc:
        raise PrinterServiceError("INVALID_DATA_URL", f"Invalid data URL for {label}.") from exc

    metadata = header[5:]
    if not metadata:
        raise PrinterServiceError("INVALID_DATA_URL", f"Invalid data URL for {label}.")

    parts = [part.strip() for part in metadata.split(";") if part.strip()]
    if "base64" not in {part.casefold() for part in parts}:
        raise PrinterServiceError(
            "UNSUPPORTED_DATA_URL",
            f"Only base64 data URLs are supported for {label}.",
        )

    declared_mime_type = normalize_mime_type(parts[0]) if parts and "/" in parts[0] else None
    return declared_mime_type, encoded


def _mime_allowed(
    mime_type: str,
    *,
    allowed_mime_types: tuple[str, ...],
    allowed_mime_prefixes: tuple[str, ...],
) -> bool:
    normalized = normalize_mime_type(mime_type)
    if normalized is None:
        return False

    if normalized in allowed_mime_types:
        return True
    return any(normalized.startswith(prefix) for prefix in allowed_mime_prefixes)


def _mime_compatible(declared: str, detected: str, *, allow_family_match: bool) -> bool:
    normalized_declared = normalize_mime_type(declared)
    normalized_detected = normalize_mime_type(detected)
    if normalized_declared is None or normalized_detected is None:
        return False

    if normalized_declared == normalized_detected:
        return True
    if allow_family_match:
        declared_family = normalized_declared.partition("/")[0]
        detected_family = normalized_detected.partition("/")[0]
        return declared_family == detected_family
    return False


def _normalize_optional_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _normalize_confidence(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
