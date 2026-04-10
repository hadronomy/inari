from __future__ import annotations

import base64
import unittest

from iot_agent.models import PrintJobRequest, PrintReceiptRequest
from iot_agent.print_jobs import ReceiptImageContent, TextDocumentContent


class PrintModelTests(unittest.TestCase):
    def test_legacy_receipt_request_accepts_base64_image(self) -> None:
        encoded = base64.b64encode(b"fake-image").decode("ascii")
        request = PrintReceiptRequest(receipt=encoded, mime_type="image/png")

        job = request.to_domain()

        self.assertIsInstance(job.content, ReceiptImageContent)
        self.assertEqual(job.content.image_bytes, b"fake-image")
        self.assertEqual(job.content.mime_type, "image/png")

    def test_generic_print_request_accepts_text_content(self) -> None:
        request = PrintJobRequest.model_validate(
            {
                "content": {
                    "kind": "text",
                    "text": "Hello printer",
                    "document_name": "Greeting",
                },
                "printer_name": "Office Printer",
            }
        )

        job = request.to_domain()

        self.assertIsInstance(job.content, TextDocumentContent)
        self.assertEqual(job.content.text, "Hello printer")
        self.assertEqual(job.content.document_name, "Greeting")
        self.assertEqual(job.printer_name, "Office Printer")


if __name__ == "__main__":
    unittest.main()
