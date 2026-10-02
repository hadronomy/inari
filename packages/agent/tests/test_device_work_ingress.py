from __future__ import annotations

import pytest
from rfc8785 import dumps as canonical_dumps
from starlette.requests import Request

from inari.local_api.ingress import (
    MAX_DOCUMENT_BYTES,
    MAX_ENVELOPE_BYTES,
    MAX_PART_HEADER_BYTES,
    MAX_WIRE_BODY_BYTES,
    DeviceWorkIngress,
    IngressError,
    IngressFailureKind,
)


def _multipart(
    boundary: str,
    parts: list[tuple[str, str, bytes] | tuple[str, str, bytes, str]],
) -> bytes:
    chunks: list[bytes] = []
    for part in parts:
        name, media_type, content = part[:3]
        filename = part[3] if len(part) == 4 else None
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f"{disposition}\r\nContent-Type: {media_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks)


def _request(
    body: bytes,
    *,
    boundary: str = "inari-test",
    content_length: int | None = None,
    chunks: tuple[bytes, ...] | None = None,
    extra_headers: tuple[tuple[bytes, bytes], ...] = (),
) -> Request:
    pending = list(chunks if chunks is not None else (body,))

    async def receive() -> dict[str, object]:
        if not pending:
            return {"type": "http.disconnect"}
        chunk = pending.pop(0)
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": bool(pending),
        }

    headers = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    headers.extend(extra_headers)
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/device-work",
            "raw_path": b"/v1/device-work",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 80),
            "root_path": "",
        },
        receive=receive,
    )


def _envelope() -> bytes:
    return canonical_dumps(
        {
            "contract_major": 1,
            "operation": "receipt_image",
            "media_type": "image/jpeg",
            "context": {"device_id": "device-1"},
        }
    )


@pytest.mark.anyio
async def test_ingress_parses_the_two_contract_parts() -> None:
    body = _multipart(
        "inari-test",
        [
            ("envelope", "application/json", _envelope()),
            ("document", "image/jpeg", b"\xff\xd8jpeg\xff\xd9"),
        ],
    )

    parsed = await DeviceWorkIngress().parse(_request(body, content_length=len(body)))

    assert parsed.envelope["operation"] == "receipt_image"
    assert parsed.envelope_bytes == _envelope()
    assert parsed.document == b"\xff\xd8jpeg\xff\xd9"


@pytest.mark.anyio
async def test_ingress_accepts_a_document_filename() -> None:
    body = _multipart(
        "inari-test",
        [
            ("envelope", "application/json", _envelope()),
            ("document", "image/jpeg", b"\xff\xd8jpeg\xff\xd9", "receipt.jpg"),
        ],
    )

    parsed = await DeviceWorkIngress().parse(_request(body, content_length=len(body)))

    assert parsed.document == b"\xff\xd8jpeg\xff\xd9"


@pytest.mark.anyio
async def test_ingress_rejects_noncanonical_json() -> None:
    body = _multipart(
        "inari-test",
        [
            (
                "envelope",
                "application/json",
                b'{"operation":"receipt_image","contract_major":1,"media_type":"image/jpeg","context":{"device_id":"device-1"}}',
            ),
            ("document", "image/jpeg", b"jpeg"),
        ],
    )

    with pytest.raises(IngressError) as error:
        await DeviceWorkIngress().parse(_request(body))

    assert error.value.code == "noncanonical_device_work_envelope"
    assert error.value.kind is IngressFailureKind.INVALID_ENVELOPE


@pytest.mark.anyio
async def test_ingress_rejects_duplicate_json_keys_and_nonfinite_numbers() -> None:
    for envelope in (
        b'{"operation":"receipt_image","operation":"receipt_image"}',
        b'{"value":NaN}',
    ):
        body = _multipart(
            "inari-test",
            [
                ("envelope", "application/json", envelope),
                ("document", "image/jpeg", b"jpeg"),
            ],
        )

        with pytest.raises(IngressError) as error:
            await DeviceWorkIngress().parse(_request(body))

        assert error.value.code == "invalid_device_work_envelope"


@pytest.mark.anyio
async def test_ingress_rejects_unknown_duplicate_and_missing_parts() -> None:
    cases = (
        [
            ("envelope", "application/json", _envelope()),
            ("other", "image/jpeg", b"jpeg"),
        ],
        [
            ("envelope", "application/json", _envelope()),
            ("envelope", "application/json", _envelope()),
            ("document", "image/jpeg", b"jpeg"),
        ],
        [("envelope", "application/json", _envelope())],
    )
    for parts in cases:
        body = _multipart("inari-test", parts)
        with pytest.raises(IngressError) as error:
            await DeviceWorkIngress().parse(_request(body))
        assert error.value.code == "invalid_device_work_parts"


@pytest.mark.anyio
async def test_ingress_rejects_media_type_mismatch() -> None:
    body = _multipart(
        "inari-test",
        [
            ("envelope", "text/plain", _envelope()),
            ("document", "image/jpeg", b"jpeg"),
        ],
    )

    with pytest.raises(IngressError) as error:
        await DeviceWorkIngress().parse(_request(body))

    assert error.value.code == "unsupported_device_work_media_type"


@pytest.mark.anyio
async def test_ingress_enforces_envelope_and_document_limits() -> None:
    oversized_envelope = b"{" + b'"x":"' + b"a" * MAX_ENVELOPE_BYTES + b'"}'
    for parts, code in (
        (
            [
                ("envelope", "application/json", oversized_envelope),
                ("document", "image/jpeg", b"jpeg"),
            ],
            "device_work_envelope_too_large",
        ),
        (
            [
                ("envelope", "application/json", _envelope()),
                ("document", "image/jpeg", b"a" * (MAX_DOCUMENT_BYTES + 1)),
            ],
            "device_work_document_too_large",
        ),
    ):
        body = _multipart("inari-test", parts)
        with pytest.raises(IngressError) as error:
            await DeviceWorkIngress().parse(_request(body))
        assert error.value.code == code
        assert error.value.kind is IngressFailureKind.TOO_LARGE


@pytest.mark.anyio
async def test_ingress_enforces_wire_limit_for_content_length_and_chunked_body() -> (
    None
):
    body = _multipart(
        "inari-test",
        [
            ("envelope", "application/json", _envelope()),
            ("document", "image/jpeg", b"jpeg"),
        ],
    )
    with pytest.raises(IngressError) as declared_error:
        await DeviceWorkIngress().parse(
            _request(body, content_length=MAX_WIRE_BODY_BYTES + 1)
        )
    assert declared_error.value.code == "device_work_body_too_large"

    with pytest.raises(IngressError) as streamed_error:
        await DeviceWorkIngress().parse(
            _request(body, chunks=(body, b"x" * (MAX_WIRE_BODY_BYTES + 1)))
        )
    assert streamed_error.value.code == "device_work_body_too_large"


@pytest.mark.anyio
async def test_ingress_rejects_missing_content_type_and_truncated_multipart() -> None:
    body = _multipart(
        "inari-test",
        [
            ("envelope", "application/json", _envelope()),
            ("document", "image/jpeg", b"jpeg"),
        ],
    )
    request = _request(body)
    request.scope["headers"] = []
    with pytest.raises(IngressError) as missing_type:
        await DeviceWorkIngress().parse(request)
    assert missing_type.value.code == "invalid_device_work_content_type"

    with pytest.raises(IngressError) as truncated:
        await DeviceWorkIngress().parse(_request(body[:-8]))
    assert truncated.value.code == "malformed_device_work"


@pytest.mark.anyio
async def test_ingress_rejects_content_length_mismatch_and_ambiguous_headers() -> None:
    body = _multipart(
        "inari-test",
        [
            ("envelope", "application/json", _envelope()),
            ("document", "image/jpeg", b"jpeg"),
        ],
    )

    for declared in (len(body) - 1, len(body) + 1):
        with pytest.raises(IngressError) as mismatch:
            await DeviceWorkIngress().parse(_request(body, content_length=declared))
        assert mismatch.value.code == "content_length_mismatch"

    with pytest.raises(IngressError) as duplicate_length:
        await DeviceWorkIngress().parse(
            _request(
                body,
                content_length=len(body),
                extra_headers=((b"content-length", str(len(body)).encode()),),
            )
        )
    assert duplicate_length.value.code == "ambiguous_request_headers"

    with pytest.raises(IngressError) as duplicate_type:
        await DeviceWorkIngress().parse(
            _request(
                body,
                extra_headers=(
                    (b"content-type", b"multipart/form-data; boundary=other"),
                ),
            )
        )
    assert duplicate_type.value.code == "ambiguous_request_headers"


@pytest.mark.anyio
async def test_ingress_rejects_non_ascii_or_unbounded_content_length() -> None:
    body = b""
    for raw_length in ("١".encode(), b"9" * 21):
        request = _request(body, extra_headers=((b"content-length", raw_length),))
        with pytest.raises(IngressError) as error:
            await DeviceWorkIngress().parse(request)
        assert error.value.code == "invalid_content_length"


@pytest.mark.anyio
async def test_ingress_rejects_ambiguous_boundary_and_part_headers() -> None:
    body = _multipart(
        "inari-test",
        [
            ("envelope", "application/json", _envelope()),
            ("document", "image/jpeg", b"jpeg"),
        ],
    )
    request = _request(body)
    request.scope["headers"] = [
        (
            b"content-type",
            b"multipart/form-data; boundary=inari-test; boundary=other",
        )
    ]
    with pytest.raises(IngressError) as duplicate_boundary:
        await DeviceWorkIngress().parse(request)
    assert duplicate_boundary.value.code == "invalid_device_work_content_type"

    oversized_boundary = b"x" * 71
    request = _request(body)
    request.scope["headers"] = [
        (b"content-type", b"multipart/form-data; boundary=" + oversized_boundary)
    ]
    with pytest.raises(IngressError):
        await DeviceWorkIngress().parse(request)

    boundary = "inari-test"
    body_with_filename = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="envelope"; filename="x"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode()
        + _envelope()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    with pytest.raises(IngressError) as filename:
        await DeviceWorkIngress().parse(_request(body_with_filename))
    assert filename.value.code == "invalid_device_work_parts"

    body_with_large_header = (
        f"--{boundary}\r\nX-Long: ".encode()
        + b"x" * (MAX_PART_HEADER_BYTES + 1)
        + b"\r\n\r\nvalue\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    with pytest.raises(IngressError) as large_header:
        await DeviceWorkIngress().parse(_request(body_with_large_header))
    assert large_header.value.code == "malformed_device_work"


@pytest.mark.anyio
async def test_ingress_rejects_deep_json_and_client_disconnect() -> None:
    nested = b"[" * 2_000 + b"0" + b"]" * 2_000
    body = _multipart(
        "inari-test",
        [("envelope", "application/json", nested), ("document", "image/jpeg", b"jpeg")],
    )
    with pytest.raises(IngressError) as deep:
        await DeviceWorkIngress().parse(_request(body))
    assert deep.value.code == "invalid_device_work_envelope"

    calls = 0

    async def receive() -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": body[:20], "more_body": True}
        return {"type": "http.disconnect"}

    request = _request(body)
    request._receive = receive
    with pytest.raises(IngressError) as disconnected:
        await DeviceWorkIngress().parse(request)
    assert disconnected.value.code == "malformed_device_work"
