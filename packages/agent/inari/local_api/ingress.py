from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import cast

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.requests import ClientDisconnect
from starlette.requests import Request

MAX_WIRE_BODY_BYTES = 16 * 1024 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_PART_HEADER_COUNT = 3
MAX_PART_HEADER_BYTES = 4_224

_BOUNDARY = re.compile(
    rb"^multipart/form-data[ \t]*;[ \t]*boundary="
    rb"(?:\"(?P<quoted>[0-9A-Za-z'()+_,./:=?-]{1,70})\"|"
    rb"(?P<plain>[0-9A-Za-z'()+_,./:=?-]{1,70}))[ \t]*$",
    re.IGNORECASE,
)

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


@dataclass(frozen=True, slots=True)
class ParsedDeviceWork:
    """The bounded envelope and document extracted from one request."""

    envelope: Mapping[str, JsonValue]
    envelope_bytes: bytes
    document: bytes


class IngressFailureKind(StrEnum):
    MALFORMED = "malformed"
    TOO_LARGE = "too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    INVALID_ENVELOPE = "invalid_envelope"


class IngressError(ValueError):
    """A content-free rejection from the Device Work wire parser."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: IngressFailureKind = IngressFailureKind.MALFORMED,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.kind = kind


Canonicalizer = Callable[[JsonValue], bytes]


class DeviceWorkIngress:
    """Parse the bounded multipart wire contract without buffering the request."""

    def __init__(self, canonicalizer: Canonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or _rfc8785_dumps

    async def parse(self, request: Request) -> ParsedDeviceWork:
        boundary = _multipart_boundary(request)
        declared_length = _content_length(request)

        envelope_bytes = bytearray()
        document_bytes = bytearray()
        parts: set[bytes] = set()
        current_name: bytes | None = None
        current_buffer: bytearray | None = None
        current_limit = 0
        wire_bytes = 0
        ended = False

        def on_part_begin() -> None:
            nonlocal current_name, current_buffer, current_limit
            current_name = None
            current_buffer = None
            current_limit = 0
            headers.clear()
            header_name.clear()
            header_value.clear()

        header_name = bytearray()
        header_value = bytearray()
        headers: dict[bytes, bytes] = {}

        def on_header_field(data: bytes, start: int, end: int) -> None:
            header_name.extend(data[start:end])

        def on_header_value(data: bytes, start: int, end: int) -> None:
            header_value.extend(data[start:end])

        def on_header_end() -> None:
            name = bytes(header_name).lower()
            if not name or name in headers:
                raise IngressError(
                    "malformed_device_work",
                    "Device Work contains invalid part headers.",
                )
            headers[name] = bytes(header_value)
            header_name.clear()
            header_value.clear()

        def on_headers_finished() -> None:
            nonlocal current_name, current_buffer, current_limit
            try:
                disposition, options = parse_options_header(
                    headers.get(b"content-disposition")
                )
            except (TypeError, ValueError) as exc:
                raise IngressError(
                    "malformed_device_work",
                    "Device Work contains invalid part headers.",
                ) from exc

            name = options.get(b"name")
            allowed_options = (
                {b"name", b"filename"}
                if name == b"document" and b"filename" in options
                else {b"name"}
            )
            if (
                disposition != b"form-data"
                or name is None
                or set(options) != allowed_options
            ):
                raise IngressError(
                    "invalid_device_work_parts",
                    "Device Work must contain named form-data parts.",
                )
            if name not in {b"envelope", b"document"} or name in parts:
                raise IngressError(
                    "invalid_device_work_parts",
                    "Device Work must contain one envelope and one document part.",
                )

            raw_media_type = headers.get(b"content-type")
            if raw_media_type is None:
                raise IngressError(
                    "unsupported_device_work_media_type",
                    "Device Work parts must declare their media type.",
                    kind=IngressFailureKind.UNSUPPORTED_MEDIA_TYPE,
                )
            try:
                media_type, media_parameters = parse_options_header(raw_media_type)
            except (TypeError, ValueError) as exc:
                raise IngressError(
                    "unsupported_device_work_media_type",
                    "Device Work part media type is invalid.",
                    kind=IngressFailureKind.UNSUPPORTED_MEDIA_TYPE,
                ) from exc
            expected_media_type = (
                b"application/json" if name == b"envelope" else b"image/jpeg"
            )
            if media_type.lower() != expected_media_type or media_parameters:
                raise IngressError(
                    "unsupported_device_work_media_type",
                    "Device Work part media type is not supported.",
                    kind=IngressFailureKind.UNSUPPORTED_MEDIA_TYPE,
                )

            unknown_headers = set(headers) - {
                b"content-disposition",
                b"content-type",
                b"content-transfer-encoding",
            }
            if unknown_headers:
                raise IngressError(
                    "invalid_device_work_parts",
                    "Device Work contains unsupported part headers.",
                )

            transfer_encoding = headers.get(b"content-transfer-encoding")
            if transfer_encoding is not None and transfer_encoding.lower() != b"binary":
                raise IngressError(
                    "unsupported_device_work_encoding",
                    "Device Work parts must use their exact binary content.",
                    kind=IngressFailureKind.UNSUPPORTED_MEDIA_TYPE,
                )

            parts.add(name)
            current_name = name
            current_buffer = envelope_bytes if name == b"envelope" else document_bytes
            current_limit = (
                MAX_ENVELOPE_BYTES if name == b"envelope" else MAX_DOCUMENT_BYTES
            )

        def on_part_data(data: bytes, start: int, end: int) -> None:
            if current_name is None or current_buffer is None:
                raise IngressError(
                    "malformed_device_work",
                    "Device Work contains data outside a named part.",
                )
            part_size = len(current_buffer) + (end - start)
            if part_size > current_limit:
                code = (
                    "device_work_envelope_too_large"
                    if current_name == b"envelope"
                    else "device_work_document_too_large"
                )
                raise IngressError(
                    code,
                    "Device Work part exceeds its content limit.",
                    kind=IngressFailureKind.TOO_LARGE,
                )
            current_buffer.extend(data[start:end])

        def on_part_end() -> None:
            if current_name is None or current_buffer is None:
                raise IngressError(
                    "malformed_device_work",
                    "Device Work contains an unnamed part.",
                )

        def on_end() -> None:
            nonlocal ended
            ended = True

        parser = MultipartParser(
            boundary,
            callbacks={
                "on_part_begin": on_part_begin,
                "on_header_field": on_header_field,
                "on_header_value": on_header_value,
                "on_header_end": on_header_end,
                "on_headers_finished": on_headers_finished,
                "on_part_data": on_part_data,
                "on_part_end": on_part_end,
                "on_end": on_end,
            },
            # The request stream is counted before each write.  MultipartParser's
            # max_size truncates a write, which would hide the wire-limit error.
            max_size=float("inf"),
            max_header_count=MAX_PART_HEADER_COUNT,
            max_header_size=MAX_PART_HEADER_BYTES,
        )

        try:
            async for chunk in request.stream():
                wire_bytes += len(chunk)
                if wire_bytes > MAX_WIRE_BODY_BYTES:
                    raise IngressError(
                        "device_work_body_too_large",
                        "Device Work request exceeds the 16 MiB limit.",
                        kind=IngressFailureKind.TOO_LARGE,
                    )
                parser.write(chunk)
            parser.finalize()
        except IngressError:
            raise
        except (ClientDisconnect, MultipartParseError, ValueError, TypeError) as exc:
            raise IngressError(
                "malformed_device_work",
                "Device Work multipart content is invalid.",
            ) from exc

        if declared_length is not None and wire_bytes != declared_length:
            raise IngressError(
                "content_length_mismatch",
                "Content-Length does not match the Device Work body.",
            )

        if not ended:
            raise IngressError(
                "malformed_device_work",
                "Device Work multipart content is incomplete.",
            )
        if parts != {b"envelope", b"document"}:
            raise IngressError(
                "invalid_device_work_parts",
                "Device Work must contain one envelope and one document part.",
            )

        parsed_envelope = _parse_canonical_envelope(
            bytes(envelope_bytes), self._canonicalizer
        )
        return ParsedDeviceWork(
            envelope=parsed_envelope,
            envelope_bytes=bytes(envelope_bytes),
            document=bytes(document_bytes),
        )


def _multipart_boundary(request: Request) -> bytes:
    raw_content_type = _single_header(request, b"content-type")
    if raw_content_type is None:
        raise IngressError(
            "invalid_device_work_content_type",
            "Device Work requires multipart/form-data.",
        )
    match = _BOUNDARY.fullmatch(raw_content_type)
    if match is None:
        raise IngressError(
            "invalid_device_work_content_type",
            "Device Work requires multipart/form-data with a boundary.",
            kind=IngressFailureKind.UNSUPPORTED_MEDIA_TYPE,
        )
    boundary = match.group("quoted") or match.group("plain")
    assert boundary is not None
    return boundary


def _content_length(request: Request) -> int | None:
    raw_length = _single_header(request, b"content-length")
    if raw_length is None:
        return None
    if (
        not raw_length
        or len(raw_length) > 20
        or any(value < ord("0") or value > ord("9") for value in raw_length)
    ):
        raise IngressError(
            "invalid_content_length",
            "Content-Length must be a non-negative decimal integer.",
        )
    length = int(raw_length)
    if length > MAX_WIRE_BODY_BYTES:
        raise IngressError(
            "device_work_body_too_large",
            "Device Work request exceeds the 16 MiB limit.",
            kind=IngressFailureKind.TOO_LARGE,
        )
    return length


def _single_header(request: Request, name: bytes) -> bytes | None:
    values = [
        value
        for header_name, value in request.scope.get("headers", ())
        if header_name.lower() == name
    ]
    if len(values) > 1:
        raise IngressError(
            "ambiguous_request_headers",
            "Device Work request headers are ambiguous.",
        )
    return values[0] if values else None


def _parse_canonical_envelope(
    content: bytes, canonicalizer: Canonicalizer
) -> Mapping[str, JsonValue]:
    try:
        text = content.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
        TypeError,
    ) as exc:
        raise IngressError(
            "invalid_device_work_envelope",
            "Device Work envelope is not valid JSON.",
            kind=IngressFailureKind.INVALID_ENVELOPE,
        ) from exc
    if not isinstance(value, dict):
        raise IngressError(
            "invalid_device_work_envelope",
            "Device Work envelope must be a JSON object.",
            kind=IngressFailureKind.INVALID_ENVELOPE,
        )
    envelope = cast(dict[str, JsonValue], value)
    try:
        canonical = canonicalizer(envelope)
    except Exception as exc:
        raise IngressError(
            "invalid_device_work_envelope",
            "Device Work envelope cannot be canonicalized.",
            kind=IngressFailureKind.INVALID_ENVELOPE,
        ) from exc
    if not isinstance(canonical, bytes) or canonical != content:
        raise IngressError(
            "noncanonical_device_work_envelope",
            "Device Work envelope must use RFC 8785 canonical JSON.",
            kind=IngressFailureKind.INVALID_ENVELOPE,
        )
    return envelope


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _rfc8785_dumps(value: JsonValue) -> bytes:
    import rfc8785

    return rfc8785.dumps(value)


__all__ = [
    "MAX_DOCUMENT_BYTES",
    "MAX_ENVELOPE_BYTES",
    "MAX_PART_HEADER_BYTES",
    "MAX_PART_HEADER_COUNT",
    "MAX_WIRE_BODY_BYTES",
    "DeviceWorkIngress",
    "IngressError",
    "IngressFailureKind",
    "ParsedDeviceWork",
]
