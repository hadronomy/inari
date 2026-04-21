from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from inari.printing.commands import CutPaper
from inari.printing.payloads import coerce_image_payload, coerce_pdf_payload
from inari.core.exceptions import PrinterServiceError
from inari.local_api.schemas import DeviceCommandRequest, PrintJobRequest
from inari.printing.jobs import ReceiptImageContent, TextDocumentContent


def test_image_payload_accepts_data_url_and_detects_mime() -> None:
    png_base64 = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pQe0AAAAASUVORK5CYII="
    )

    payload = coerce_image_payload(png_base64, label="receipt image")

    assert payload.source == "data_url"
    assert "image/png" in payload.declared_mime_types
    assert payload.mime_type == "image/png"


def test_pdf_payload_rejects_non_pdf_bytes() -> None:
    encoded = base64.b64encode(b"not a pdf").decode("ascii")

    with pytest.raises(PrinterServiceError, match="PDF document"):
        coerce_pdf_payload(encoded, label="PDF document")


def test_image_payload_rejects_conflicting_declared_mime_types() -> None:
    png_base64 = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pQe0AAAAASUVORK5CYII="
    )

    with pytest.raises(PrinterServiceError, match="Conflicting MIME type declarations"):
        coerce_image_payload(
            png_base64,
            label="receipt image",
            declared_mime_type="image/jpeg",
        )


def test_generic_print_request_accepts_nested_target_and_options() -> None:
    request = PrintJobRequest.model_validate(
        {
            "content": {
                "kind": "text",
                "text": "Hello printer",
                "document_name": "Greeting",
            },
            "target": {"printer_name": "Office Printer"},
            "options": {"transport": "text", "open_cash_drawer": True},
        }
    )

    operation = request.to_operation()
    job = operation.job

    assert isinstance(job.content, TextDocumentContent)
    assert job.content.text == "Hello printer"
    assert job.content.document_name == "Greeting"
    assert job.printer_name == "Office Printer"
    assert job.transport == "text"
    assert job.open_drawer is True
    assert operation.target.printer_name == "Office Printer"


def test_generic_print_request_accepts_binary_wrapper() -> None:
    request = PrintJobRequest.model_validate(
        {
            "content": {
                "kind": "receipt_image",
                "binary": {
                    "base64": (
                        "data:image/png;base64,"
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pQe0AAAAASUVORK5CYII="
                    ),
                    "declared_mime_type": "image/png",
                },
                "document_name": "POS Ticket",
            }
        }
    )

    operation = request.to_operation()
    job = operation.job

    assert isinstance(job.content, ReceiptImageContent)
    assert job.content.document_name == "POS Ticket"
    assert job.content.mime_type == "image/png"


def test_generic_print_request_rejects_legacy_option_alias() -> None:
    with pytest.raises(ValidationError):
        PrintJobRequest.model_validate(
            {
                "content": {
                    "kind": "text",
                    "text": "Hello printer",
                },
                "options": {"open_drawer": True},
            }
        )


def test_generic_print_request_rejects_legacy_binary_alias() -> None:
    with pytest.raises(ValidationError):
        PrintJobRequest.model_validate(
            {
                "content": {
                    "kind": "receipt_image",
                    "binary": {
                        "base64": (
                            "data:image/png;base64,"
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pQe0AAAAASUVORK5CYII="
                        ),
                        "mime_type": "image/png",
                    },
                }
            }
        )


def test_device_command_request_accepts_typed_command() -> None:
    request = DeviceCommandRequest.model_validate(
        {
            "target": {"device_id": "dev_test", "printer_name": "Kitchen Printer"},
            "command": {
                "kind": "cut_paper",
                "mode": "full",
            },
        }
    )

    operation = request.to_operation()

    assert operation.target.device_id == "dev_test"
    assert operation.target.printer_name == "Kitchen Printer"
    assert isinstance(operation.command, CutPaper)
    assert operation.command.kind == "cut_paper"
    assert operation.command.mode == "full"
