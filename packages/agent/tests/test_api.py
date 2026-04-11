from __future__ import annotations

import unittest
from unittest.mock import Mock

from iot_agent.api import execute_printer_command, submit_print_job, system_status
from iot_agent.config import AgentSettings
from iot_agent.container import AgentContainer
from iot_agent.drivers import DriverRegistry
from iot_agent.exceptions import PrinterServiceError
from iot_agent.main import create_app
from iot_agent.models import PrintJobRequest, PrinterCommandRequest
from iot_agent.printers import PrintJobResult, PrinterCapabilities, PrinterDevice, PrinterTransport
from fastapi.testclient import TestClient


class ApiShapeTests(unittest.TestCase):
    def test_system_status_reports_supported_content_and_command_kinds(self) -> None:
        printer = PrinterDevice(
            name="EPSON TM-T20III",
            driver_key="tests.fake-printers",
            is_default=True,
            preferred_transport=PrinterTransport.RAW,
            capabilities=PrinterCapabilities(raw=True, text=True, documents=True, cash_drawer=True),
        )
        printer_service = Mock()
        printer_service.list_printers.return_value = (printer,)

        response = system_status(printer_service=printer_service)

        self.assertEqual(response.service.version, "1.6.0a1")
        self.assertEqual(response.printers.default_printer_name, printer.name)
        self.assertIn("receipt_image", response.supported_content_kinds)
        self.assertIn("cut_paper", response.supported_printer_commands)

    def test_submit_print_job_uses_nested_target_and_options_shape(self) -> None:
        printer = PrinterDevice(
            name="Back Office",
            driver_key="tests.fake-printers",
            preferred_transport=PrinterTransport.TEXT,
            capabilities=PrinterCapabilities(raw=False, text=True, documents=True, cash_drawer=False),
        )
        printer_service = Mock()
        printer_service.print_job.return_value = PrintJobResult(
            printer=printer,
            transport=PrinterTransport.TEXT,
            bytes_written=12,
            job_id=7,
        )
        request = PrintJobRequest.model_validate(
            {
                "content": {
                    "kind": "text",
                    "text": "Hello printer",
                    "document_name": "Greeting",
                },
                "target": {"printer_name": printer.name},
                "options": {"transport": "text"},
            }
        )

        response = submit_print_job(request=request, printer_service=printer_service)

        self.assertEqual(response.operation, "print_job")
        self.assertEqual(response.result.printer.printer_name, printer.name)
        self.assertEqual(response.result.transport, "text")
        self.assertEqual(response.result.job_id, 7)

    def test_execute_printer_command_returns_typed_operation_response(self) -> None:
        printer = PrinterDevice(
            name="Kitchen Printer",
            driver_key="tests.fake-printers",
            preferred_transport=PrinterTransport.RAW,
            capabilities=PrinterCapabilities(raw=True, text=True, documents=True, cash_drawer=True),
        )
        printer_service = Mock()
        printer_service.cut_paper.return_value = PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=3,
            job_id=9,
        )
        request = PrinterCommandRequest.model_validate(
            {
                "target": {"printer_name": printer.name},
                "command": {"kind": "cut_paper", "mode": "full"},
            }
        )

        response = execute_printer_command(request=request, printer_service=printer_service)

        self.assertEqual(response.operation, "cut_paper")
        self.assertEqual(response.result.printer.driver, printer.driver_key)
        self.assertEqual(response.result.bytes_written, 3)

    def test_validation_errors_use_unified_problem_details_shape(self) -> None:
        client = make_test_client(Mock())

        response = client.post("/print-jobs", json={})

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "REQUEST_VALIDATION_FAILED")
        self.assertEqual(payload["title"], "Request Validation Failed")
        self.assertEqual(payload["status"], 422)
        self.assertEqual(payload["type"], "urn:iot-agent:error:request-validation-failed")
        self.assertIn("errors", payload)
        self.assertEqual(payload["errors"][0]["source"]["pointer"], "/content")

    def test_agent_errors_use_unified_problem_details_shape(self) -> None:
        printer_service = Mock()
        printer_service.print_job.side_effect = PrinterServiceError(
            "MIME_TYPE_MISMATCH",
            "Declared MIME type 'image/jpeg' does not match detected MIME type 'image/png' for receipt image.",
        )
        client = make_test_client(printer_service)

        response = client.post(
            "/print-jobs",
            json={
                "content": {
                    "kind": "text",
                    "text": "Hello printer",
                    "document_name": "Greeting",
                }
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "MIME_TYPE_MISMATCH")
        self.assertEqual(payload["title"], "MIME Type Mismatch")
        self.assertEqual(payload["status"], 400)
        self.assertEqual(payload["type"], "urn:iot-agent:error:mime-type-mismatch")
        self.assertIn("detail", payload)
        self.assertNotIn("message", payload)

    def test_framework_http_errors_use_unified_problem_details_shape(self) -> None:
        client = make_test_client(Mock())

        response = client.get("/missing-route")

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "HTTP_404")
        self.assertEqual(payload["title"], "Not Found")
        self.assertEqual(payload["status"], 404)
        self.assertEqual(payload["type"], "urn:iot-agent:error:http-404")
        self.assertEqual(payload["details"]["path"], "/missing-route")


def make_test_client(printer_service: Mock) -> TestClient:
    container = AgentContainer(
        settings=AgentSettings(),
        driver_registry=DriverRegistry(drivers=()),
        printer_service=printer_service,
    )
    return TestClient(create_app(container=container))


if __name__ == "__main__":
    unittest.main()
