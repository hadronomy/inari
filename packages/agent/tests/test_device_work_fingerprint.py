from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta, timezone
from dataclasses import fields

import pytest

from inari_print_contracts.fingerprint import (
    DeviceWorkFingerprintInput,
    fingerprint_device_work,
)
from inari.documents import DocumentKind


def work(**changes: object) -> DeviceWorkFingerprintInput:
    values: dict[str, object] = {
        "contract_major": 1,
        "operation": DocumentKind.RECEIPT_IMAGE,
        "device_id": "dev_receipt_1",
        "media_type": "image/jpeg",
        "document": b"receipt-bytes",
        "options": {"density": 203, "cut": True},
        "expires_at": datetime(2026, 8, 27, 12, 30, 0, 123456, tzinfo=UTC),
    }
    values.update(changes)
    return DeviceWorkFingerprintInput(**values)


def test_each_stable_field_changes_the_digest() -> None:
    baseline = fingerprint_device_work(work())

    variants = (
        work(contract_major=2),
        work(operation=DocumentKind.REPORT_PDF),
        work(device_id="dev_receipt_2"),
        work(media_type="application/pdf"),
        work(document=b"another-receipt"),
        work(options={"density": 300, "cut": True}),
        work(expires_at=datetime(2026, 8, 27, 12, 30, 1, tzinfo=UTC)),
    )

    assert all(fingerprint_device_work(value) != baseline for value in variants)


def test_options_use_rfc8785_key_order() -> None:
    first = work(options={"b": 2, "a": 1})
    second = work(options={"a": 1, "b": 2})

    assert fingerprint_device_work(first) == fingerprint_device_work(second)


def test_deadline_is_normalized_to_utc_with_fixed_microseconds() -> None:
    utc = work(
        expires_at=datetime(2026, 8, 27, 12, 30, 0, 123456, tzinfo=UTC)
    )
    offset = work(
        expires_at=datetime(
            2026,
            8,
            27,
            14,
            30,
            0,
            123456,
            tzinfo=timezone(timedelta(hours=2)),
        )
    )

    assert fingerprint_device_work(utc) == fingerprint_device_work(offset)


def test_digest_api_has_no_request_metadata_fields() -> None:
    names = {field.name for field in fields(DeviceWorkFingerprintInput)}
    assert names == {
        "contract_major",
        "operation",
        "device_id",
        "media_type",
        "document",
        "options",
        "expires_at",
    }
    assert "idempotency_key" not in names
    assert "credentials" not in names
    assert "context" not in names
    assert "timestamp" not in names
    assert "transport" not in names

    signature = inspect.signature(fingerprint_device_work)
    assert "idempotency_key" not in signature.parameters
    assert "credentials" not in signature.parameters
    assert "context" not in signature.parameters


def test_length_prefixes_prevent_field_boundary_collisions() -> None:
    first = work(operation=DocumentKind.RECEIPT_IMAGE, device_id="report_pdf")
    second = work(operation=DocumentKind.REPORT_PDF, device_id="receipt_image")

    assert fingerprint_device_work(first) != fingerprint_device_work(second)


def test_length_prefixes_preserve_binary_document_boundaries() -> None:
    first = work(document=b"ab")
    second = work(document=b"a" + b"b")

    assert fingerprint_device_work(first) == fingerprint_device_work(second)


def test_naive_deadline_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        fingerprint_device_work(
            work(expires_at=datetime(2026, 8, 27, 12, 30, 0))
        )


def test_operation_and_media_type_are_normalized() -> None:
    assert fingerprint_device_work(
        work(operation="receipt_image", media_type=" IMAGE/JPEG ")
    ) == fingerprint_device_work(
        work(operation=DocumentKind.RECEIPT_IMAGE, media_type="image/jpeg")
    )


def test_unknown_operation_is_rejected() -> None:
    with pytest.raises(ValueError, match="supported DocumentKind"):
        fingerprint_device_work(work(operation="raw_bytes"))


def test_digest_is_a_sha256_digest() -> None:
    digest = fingerprint_device_work(work())

    assert isinstance(digest, bytes)
    assert len(digest) == 32
