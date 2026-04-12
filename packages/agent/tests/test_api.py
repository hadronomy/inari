from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field, replace
from datetime import timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient

from iot_agent.config import AgentSettings
from iot_agent.container import AgentContainer
from iot_agent.drivers import DriverRegistry
from iot_agent.exceptions import AgentError
from iot_agent.main import create_app
from iot_agent.runtime.events import EventHub
from iot_agent.runtime.models import (
    DeviceConnectionState,
    DeviceRecord,
    JobAttemptRecord,
    JobEventRecord,
    JobKind,
    JobRecord,
    JobState,
    utc_now,
)
from iot_agent.printers import PrinterCapabilities, PrinterDevice, PrinterTransport


@dataclass(slots=True)
class StubRuntimeSupervisor:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@dataclass(slots=True)
class StubDeviceCatalog:
    devices: tuple[DeviceRecord, ...]
    device_events: tuple[JobEventRecord, ...] = ()

    def list_devices(self) -> tuple[DeviceRecord, ...]:
        return self.devices

    def get_device(self, device_id: str) -> DeviceRecord | None:
        return next((device for device in self.devices if device.id == device_id), None)

    def list_device_events(self, device_id: str, *, limit: int = 50):
        return self.device_events[:limit]


@dataclass(slots=True)
class StubJobService:
    jobs: dict[str, JobRecord]
    queue_counts_payload: dict[str, int] = field(default_factory=lambda: {"queued": 1})
    job_events: tuple[JobEventRecord, ...] = ()
    job_attempts: tuple[JobAttemptRecord, ...] = ()
    enqueue_print_error: Exception | None = None
    enqueue_command_error: Exception | None = None
    submitted_print_operation: object | None = None
    submitted_command_operation: object | None = None

    async def enqueue_print(self, operation):
        if self.enqueue_print_error is not None:
            raise self.enqueue_print_error
        self.submitted_print_operation = operation
        return next(iter(self.jobs.values()))

    async def enqueue_command(self, operation):
        if self.enqueue_command_error is not None:
            raise self.enqueue_command_error
        self.submitted_command_operation = operation
        return next(iter(self.jobs.values()))

    def list_jobs(self, *, state=None, limit: int = 100):
        jobs = list(self.jobs.values())
        if state is not None:
            jobs = [job for job in jobs if job.state is state]
        return tuple(jobs[:limit])

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def list_job_history(self, job_id: str):
        return self.job_events

    def list_job_attempts(self, job_id: str):
        return self.job_attempts

    def queue_counts(self):
        return dict(self.queue_counts_payload)

    async def cancel(self, job_id: str) -> JobRecord:
        job = self.jobs[job_id]
        cancelled = replace(job, state=JobState.CANCELLED, finished_at=utc_now(), updated_at=utc_now())
        self.jobs[job_id] = cancelled
        return cancelled


class ApiShapeTests(unittest.TestCase):
    def test_system_status_reports_device_and_queue_summary(self) -> None:
        container = make_test_container()
        client = TestClient(create_app(container=container))

        response = client.get("/system/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service"]["version"], "1.8.0a1")
        self.assertEqual(payload["devices"]["count"], 1)
        self.assertEqual(
            payload["devices"]["default_device"],
            {"id": next(iter(container.device_catalog.devices)).id, "name": "Kitchen Printer"},
        )
        self.assertEqual(payload["queue"]["queued"], 1)
        self.assertIn("receipt_image", payload["supported_content_kinds"])
        self.assertIn("cut_paper", payload["supported_device_commands"])

    def test_list_devices_uses_semantically_refined_shape(self) -> None:
        container = make_test_container()
        client = TestClient(create_app(container=container))

        response = client.get("/devices")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("ok", payload)
        self.assertEqual(
            payload["summary"]["default_device"],
            {"id": next(iter(container.device_catalog.devices)).id, "name": "Kitchen Printer"},
        )
        device = payload["devices"][0]
        self.assertEqual(device["driver_key"], "tests.fake-printers")
        self.assertEqual(device["device_class"], "physical")
        self.assertEqual(device["connection"]["state"], "online")
        self.assertIn("observed_at", device["connection"])
        self.assertEqual(device["printer"]["supported_transports"], ["raw", "text", "document"])
        self.assertEqual(device["printer"]["capabilities"], ["cash_drawer"])

    def test_list_devices_marks_virtual_windows_printers(self) -> None:
        device = DeviceRecord.from_printer(
            PrinterDevice(
                name="Microsoft Print to PDF",
                driver_key="windows.printers",
                capabilities=PrinterCapabilities(raw=False, text=True, documents=True, cash_drawer=False),
            ),
            connection_state=DeviceConnectionState.ONLINE,
        )
        client = TestClient(create_app(container=make_test_container(devices=(device,))))

        response = client.get("/devices")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["devices"][0]["device_class"], "virtual")

    def test_docs_route_serves_scalar_reference(self) -> None:
        client = TestClient(create_app(container=make_test_container()))

        response = client.get("/docs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("scalar", response.text.lower())
        self.assertNotIn("swagger ui", response.text.lower())

    def test_redoc_route_is_disabled(self) -> None:
        client = TestClient(create_app(container=make_test_container()))

        response = client.get("/redoc")

        self.assertEqual(response.status_code, 404)

    def test_submit_print_job_returns_queued_job_resource(self) -> None:
        container = make_test_container()
        client = TestClient(create_app(container=container))

        response = client.post(
            "/print-jobs",
            json={
                "content": {
                    "kind": "text",
                    "text": "Hello printer",
                    "document_name": "Greeting",
                },
                "target": {"printer_name": "Kitchen Printer"},
                "options": {"transport": "text"},
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["job"]["kind"], "print_job")
        self.assertEqual(payload["job"]["state"], "queued")
        self.assertEqual(payload["job"]["target"]["device_name"], "Kitchen Printer")
        self.assertEqual(container.job_service.submitted_print_operation.target.printer_name, "Kitchen Printer")

    def test_submit_device_command_returns_queued_job_resource(self) -> None:
        container = make_test_container(job_kind=JobKind.COMMAND, operation="cut_paper", command_kind="cut_paper")
        client = TestClient(create_app(container=container))

        response = client.post(
            "/device-commands",
            json={
                "target": {"printer_name": "Kitchen Printer"},
                "command": {"kind": "cut_paper", "mode": "full"},
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["job"]["kind"], "device_command")
        self.assertEqual(payload["job"]["operation"], "cut_paper")
        self.assertEqual(container.job_service.submitted_command_operation.command.kind, "cut_paper")

    def test_job_history_response_includes_attempts_and_events(self) -> None:
        container = make_test_container()
        client = TestClient(create_app(container=container))
        job_id = next(iter(container.job_service.jobs))

        response = client.get(f"/jobs/{job_id}/history")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["job"]["id"], job_id)
        self.assertEqual(payload["attempts"][0]["attempt_number"], 1)
        self.assertEqual(payload["events"][0]["event_type"], "job.queued")

    def test_list_jobs_serializes_job_execution_results(self) -> None:
        container = make_test_container()
        job_id = next(iter(container.job_service.jobs))
        completed = replace(
            container.job_service.jobs[job_id],
            state=JobState.SUCCEEDED,
            result_payload={
                "printer": {
                    "device_id": "dev_test",
                    "printer_name": "Kitchen Printer",
                    "driver_key": "tests.fake-printers",
                    "is_default": True,
                },
                "transport": "raw",
                "bytes_written": 128,
                "device_job_id": 42,
            },
        )
        container.job_service.jobs[job_id] = completed
        client = TestClient(create_app(container=container))

        response = client.get("/jobs")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["jobs"][0]["result"]["target"]["printer_name"], "Kitchen Printer")
        self.assertEqual(payload["jobs"][0]["result"]["target"]["driver_key"], "tests.fake-printers")
        self.assertEqual(payload["jobs"][0]["result"]["bytes_written"], 128)

    def test_events_websocket_connects_successfully(self) -> None:
        client = TestClient(create_app(container=make_test_container()))

        with client.websocket_connect("/events") as websocket:
            payload = websocket.receive_json()

        self.assertEqual(payload["kind"], "snapshot")
        self.assertEqual(payload["status"]["service"]["name"], "IoT Agent")
        self.assertEqual(payload["status"]["queue"]["queued"], 1)

    def test_events_websocket_streams_snapshot_backed_updates(self) -> None:
        container = make_test_container()
        client = TestClient(create_app(container=container))
        event = JobEventRecord(
            sequence=2,
            resource_id="job_123",
            event_type="job.failed",
            occurred_at=utc_now(),
            payload={"job_id": "job_123", "error_detail": "Printer offline"},
        )

        with client.websocket_connect("/events") as websocket:
            websocket.receive_json()
            asyncio.run(container.event_hub.publish(event))
            payload = websocket.receive_json()

        self.assertEqual(payload["kind"], "event_update")
        self.assertEqual(payload["event"]["event_type"], "job.failed")
        self.assertEqual(payload["event"]["payload"]["error_detail"], "Printer offline")
        self.assertEqual(payload["status"]["queue"]["queued"], 1)

    def test_validation_errors_use_unified_problem_details_shape(self) -> None:
        client = TestClient(create_app(container=make_test_container()))

        response = client.post("/print-jobs", json={})

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "REQUEST_VALIDATION_FAILED")
        self.assertEqual(payload["type"], "urn:iot-agent:error:request-validation-failed")
        self.assertEqual(payload["errors"][0]["source"]["pointer"], "/content")

    def test_agent_errors_use_unified_problem_details_shape(self) -> None:
        container = make_test_container()
        container.job_service.enqueue_print_error = AgentError(
            "DEVICE_NOT_FOUND",
            "Device 'dev_missing' was not found.",
            status_code=404,
        )
        client = TestClient(create_app(container=container))

        response = client.post(
            "/print-jobs",
            json={"content": {"kind": "text", "text": "Hello printer"}},
        )

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "DEVICE_NOT_FOUND")
        self.assertEqual(payload["title"], "Device Not Found")
        self.assertEqual(payload["type"], "urn:iot-agent:error:device-not-found")

    def test_framework_http_errors_use_unified_problem_details_shape(self) -> None:
        client = TestClient(create_app(container=make_test_container()))

        response = client.get("/missing-route")

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "HTTP_404")
        self.assertEqual(payload["details"]["path"], "/missing-route")

    def test_removed_endpoints_are_not_exposed(self) -> None:
        client = TestClient(create_app(container=make_test_container()))

        for method, path in (
            ("get", "/printers"),
            ("get", "/devices/printers"),
            ("post", "/printer-commands"),
            ("post", "/print"),
            ("post", "/print_receipt"),
        ):
            response = getattr(client, method)(path)
            self.assertEqual(response.status_code, 404, path)


def make_test_container(
    *,
    job_kind: JobKind = JobKind.PRINT,
    operation: str = "print_job",
    command_kind: str | None = None,
    devices: tuple[DeviceRecord, ...] | None = None,
) -> AgentContainer:
    default_device = DeviceRecord.from_printer(
        PrinterDevice(
            name="Kitchen Printer",
            driver_key="tests.fake-printers",
            is_default=True,
            preferred_transport=PrinterTransport.RAW,
            capabilities=PrinterCapabilities(raw=True, text=True, documents=True, cash_drawer=True),
        ),
        connection_state=DeviceConnectionState.ONLINE,
    )
    devices = devices or (default_device,)
    device = devices[0]
    now = utc_now()
    job = JobRecord(
        id="job_123",
        kind=job_kind,
        operation=operation,
        device_id=device.id,
        device_kind=device.kind,
        device_name=device.name,
        state=JobState.QUEUED,
        request_payload={"job": {"content": {"kind": "text", "text": "Hello printer"}}},
        request_metadata={"source": "test"},
        content_kind="text" if job_kind is JobKind.PRINT else None,
        command_kind=command_kind,
        attempt_count=0,
        max_attempts=3,
        created_at=now,
        updated_at=now,
        queued_at=now,
        next_run_at=now + timedelta(seconds=1),
    )
    attempt = JobAttemptRecord(
        id=1,
        job_id=job.id,
        attempt_number=1,
        state=JobState.RUNNING,
        started_at=now,
    )
    event = JobEventRecord(
        sequence=1,
        resource_id=job.id,
        event_type="job.queued",
        occurred_at=now,
        payload={"job_id": job.id},
    )
    return AgentContainer(
        settings=AgentSettings(),
        driver_registry=DriverRegistry(drivers=()),
        printer_service=Mock(),
        event_hub=EventHub(),
        device_catalog=StubDeviceCatalog(devices=(device,)),
        job_service=StubJobService(
            jobs={job.id: job},
            queue_counts_payload={"queued": 1},
            job_attempts=(attempt,),
            job_events=(event,),
        ),
        runtime_supervisor=StubRuntimeSupervisor(),
    )


if __name__ == "__main__":
    unittest.main()
