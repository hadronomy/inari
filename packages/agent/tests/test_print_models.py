from __future__ import annotations

import base64
import unittest

from iot_agent.binary_payloads import BinaryPayload, coerce_image_payload, coerce_pdf_payload
from iot_agent.exceptions import PrinterServiceError
from iot_agent.models import PrintJobRequest, PrintReceiptRequest, PrinterCommandRequest
from iot_agent.print_jobs import ReceiptImageContent, TextDocumentContent


class BinaryPayloadTests(unittest.TestCase):
    def test_image_payload_accepts_data_url_and_detects_mime(self) -> None:
        png_base64 = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pQe0AAAAASUVORK5CYII="
        )

        payload = coerce_image_payload(png_base64, label="receipt image")

        self.assertEqual(payload.source, "data_url")
        self.assertIn("image/png", payload.declared_mime_types)
        self.assertEqual(payload.mime_type, "image/png")

    def test_pdf_payload_rejects_non_pdf_bytes(self) -> None:
        encoded = base64.b64encode(b"not a pdf").decode("ascii")

        with self.assertRaisesRegex(PrinterServiceError, "PDF document"):
            coerce_pdf_payload(encoded, label="PDF document")

    def test_image_payload_rejects_conflicting_declared_mime_types(self) -> None:
        png_base64 = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pQe0AAAAASUVORK5CYII="
        )

        with self.assertRaisesRegex(PrinterServiceError, "Conflicting MIME type declarations"):
            coerce_image_payload(
                png_base64,
                label="receipt image",
                declared_mime_type="image/jpeg",
            )


class PrintModelTests(unittest.TestCase):
    def test_legacy_receipt_request_accepts_base64_image(self) -> None:
        png_base64 = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pQe0AAAAASUVORK5CYII="
        )
        request = PrintReceiptRequest(receipt=png_base64)

        job = request.to_domain()

        self.assertIsInstance(job.content, ReceiptImageContent)
        self.assertIsInstance(job.content.binary_payload, BinaryPayload)
        self.assertEqual(job.content.mime_type, "image/png")

    def test_generic_print_request_accepts_nested_target_and_options(self) -> None:
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

        job = request.to_domain()

        self.assertIsInstance(job.content, TextDocumentContent)
        self.assertEqual(job.content.text, "Hello printer")
        self.assertEqual(job.content.document_name, "Greeting")
        self.assertEqual(job.printer_name, "Office Printer")
        self.assertEqual(job.transport, "text")
        self.assertTrue(job.open_drawer)

    def test_generic_print_request_accepts_binary_wrapper(self) -> None:
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

        job = request.to_domain()

        self.assertIsInstance(job.content, ReceiptImageContent)
        self.assertEqual(job.content.document_name, "POS Ticket")
        self.assertEqual(job.content.mime_type, "image/png")

    def test_printer_command_request_accepts_typed_command(self) -> None:
        request = PrinterCommandRequest.model_validate(
            {
                "target": {"printer_name": "Kitchen Printer"},
                "command": {
                    "kind": "cut_paper",
                    "mode": "full",
                },
            }
        )

        self.assertEqual(request.target.printer_name, "Kitchen Printer")
        self.assertEqual(request.command.kind, "cut_paper")
        self.assertEqual(request.command.mode, "full")


if __name__ == "__main__":
    unittest.main()
