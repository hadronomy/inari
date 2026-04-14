from __future__ import annotations

import anyio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp

import pytest
from asgi_lifespan import LifespanManager
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from iot_agent.config import AgentSettings
from iot_agent.container import AgentContainer
from iot_agent.drivers import DriverRegistry
from iot_agent.exceptions import AgentError
from iot_agent.gateway.models import UpstreamConnectionState, UpstreamStatus
from iot_agent.main import create_app
from iot_agent.printers import PrinterCapabilities, PrinterDevice, PrinterTransport
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
from iot_agent.security.models import (
    AccessScope,
    AgentIdentity,
    AuthenticatedPrincipal,
    GatewayExposure,
    GatewayMode,
    IssuedToken,
    PrincipalKind,
)
from iot_agent.version import API_VERSION


@dataclass(slots=True)
class StubRuntimeSupervisor:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@dataclass(slots=True)
class StubApplicationSupervisor:
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


@dataclass(slots=True)
class StubAuthorizationService:
    issued_tokens: dict[str, AuthenticatedPrincipal] = field(default_factory=dict)

    def issue_loopback_token(self, connection, *, client_name: str, requested_scopes=None):
        scopes = tuple(requested_scopes or tuple(AccessScope))
        expires_at = datetime.now(tz=UTC) + timedelta(hours=1)
        token_value = f"token::{client_name}::{len(self.issued_tokens) + 1}"
        principal = AuthenticatedPrincipal(
            subject=f"local:{client_name}",
            principal_kind=PrincipalKind.LOCAL_CLIENT,
            scopes=frozenset(scopes),
            issuer="urn:test:issuer",
            audience="iot-agent.local",
            token_id=token_value,
            expires_at=expires_at,
        )
        self.issued_tokens[token_value] = principal
        return IssuedToken(
            access_token=token_value,
            expires_at=expires_at,
            scopes=scopes,
            subject=principal.subject,
            principal_kind=principal.principal_kind,
        )

    def authenticate_connection(self, connection):
        authorization = connection.headers.get("authorization")
        if not authorization:
            raise AgentError("AUTHENTICATION_REQUIRED", "A bearer access token is required for this endpoint.", status_code=401)
        _, _, token = authorization.partition(" ")
        principal = self.issued_tokens.get(token.strip())
        if principal is None:
            raise AgentError("INVALID_ACCESS_TOKEN", "The supplied access token is invalid.", status_code=401)
        return principal

    def require_scopes(self, principal: AuthenticatedPrincipal, scopes):
        required = tuple(scopes)
        if not principal.has_scopes(required):
            raise AgentError("INSUFFICIENT_SCOPE", "The access token does not include the required scopes for this endpoint.", status_code=403)
        return principal


@dataclass(slots=True)
class StubGatewayService:
    settings: AgentSettings

    def get_identity(self):
        return AgentIdentity(
            agent_id="agt_test",
            key_id="kid_test",
            algorithm="Ed25519",
            public_jwk={"kty": "OKP", "crv": "Ed25519", "kid": "kid_test", "x": "abc"},
            created_at=utc_now(),
        )

    def get_upstream_status(self):
        return UpstreamStatus(
            mode=self.settings.gateway_mode,
            state=(
                UpstreamConnectionState.DISCONNECTED
                if self.settings.gateway_mode is GatewayMode.MANAGED
                else UpstreamConnectionState.DISABLED
            ),
            base_url=self.settings.upstream_base_url,
            detail="Local gateway mode is active.",
        )


@dataclass(slots=True)
class StubPrinterService:
    pass


@asynccontextmanager
async def async_client_for(container: AgentContainer):
    app = create_app(container=container)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield client


async def auth_headers(client: AsyncClient, *, requested_scopes: tuple[str, ...] | None = None) -> dict[str, str]:
    payload: dict[str, object] = {"client_name": "test-client"}
    if requested_scopes is not None:
        payload["requested_scopes"] = list(requested_scopes)
    response = await client.post("/auth/local-token", json=payload)
    response.raise_for_status()
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def sync_auth_headers(client: TestClient, *, requested_scopes: tuple[str, ...] | None = None) -> dict[str, str]:
    payload: dict[str, object] = {"client_name": "test-client"}
    if requested_scopes is not None:
        payload["requested_scopes"] = list(requested_scopes)
    response = client.post("/auth/local-token", json=payload)
    response.raise_for_status()
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_system_status_reports_device_and_queue_summary(mocker) -> None:
    container = make_test_container(mocker=mocker)

    async with async_client_for(container) as client:
        response = await client.get("/system/status", headers=await auth_headers(client))

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"]["version"] == API_VERSION
    assert payload["devices"]["count"] == 1
    assert payload["devices"]["default_device"] == {
        "id": next(iter(container.device_catalog.devices)).id,
        "name": "Kitchen Printer",
    }
    assert payload["queue"]["queued"] == 1
    assert "receipt_image" in payload["supported_content_kinds"]
    assert "cut_paper" in payload["supported_device_commands"]


@pytest.mark.anyio
async def test_list_devices_uses_semantically_refined_shape(mocker) -> None:
    container = make_test_container(mocker=mocker)

    async with async_client_for(container) as client:
        response = await client.get("/devices", headers=await auth_headers(client))

    assert response.status_code == 200
    payload = response.json()
    assert "ok" not in payload
    assert payload["summary"]["default_device"] == {
        "id": next(iter(container.device_catalog.devices)).id,
        "name": "Kitchen Printer",
    }
    device = payload["devices"][0]
    assert device["driver_key"] == "tests.fake-printers"
    assert device["device_class"] == "physical"
    assert device["connection"]["state"] == "online"
    assert "observed_at" in device["connection"]
    assert device["printer"]["supported_transports"] == ["raw", "text", "document"]
    assert device["printer"]["capabilities"] == ["cash_drawer"]


@pytest.mark.anyio
async def test_list_devices_marks_virtual_windows_printers(mocker) -> None:
    device = DeviceRecord.from_printer(
        PrinterDevice(
            name="Microsoft Print to PDF",
            driver_key="windows.printers",
            capabilities=PrinterCapabilities(raw=False, text=True, documents=True, cash_drawer=False),
        ),
        connection_state=DeviceConnectionState.ONLINE,
    )

    async with async_client_for(make_test_container(devices=(device,), mocker=mocker)) as client:
        response = await client.get("/devices", headers=await auth_headers(client))

    assert response.status_code == 200
    assert response.json()["devices"][0]["device_class"] == "virtual"


@pytest.mark.anyio
async def test_docs_route_serves_scalar_reference(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        response = await client.get("/docs")

    assert response.status_code == 200
    assert "scalar" in response.text.lower()
    assert "swagger ui" not in response.text.lower()


@pytest.mark.anyio
async def test_redoc_route_is_disabled(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        response = await client.get("/redoc")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_submit_print_job_returns_queued_job_resource(mocker) -> None:
    container = make_test_container(mocker=mocker)

    async with async_client_for(container) as client:
        response = await client.post(
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
            headers=await auth_headers(client),
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["ok"] is True
    assert payload["job"]["kind"] == "print_job"
    assert payload["job"]["state"] == "queued"
    assert payload["job"]["target"]["device_name"] == "Kitchen Printer"
    assert container.job_service.submitted_print_operation.target.printer_name == "Kitchen Printer"


@pytest.mark.anyio
async def test_submit_device_command_returns_queued_job_resource(mocker) -> None:
    container = make_test_container(job_kind=JobKind.COMMAND, operation="cut_paper", command_kind="cut_paper", mocker=mocker)

    async with async_client_for(container) as client:
        response = await client.post(
            "/device-commands",
            json={
                "target": {"printer_name": "Kitchen Printer"},
                "command": {"kind": "cut_paper", "mode": "full"},
            },
            headers=await auth_headers(client),
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["job"]["kind"] == "device_command"
    assert payload["job"]["operation"] == "cut_paper"
    assert container.job_service.submitted_command_operation.command.kind == "cut_paper"


@pytest.mark.anyio
async def test_job_history_response_includes_attempts_and_events(mocker) -> None:
    container = make_test_container(mocker=mocker)
    job_id = next(iter(container.job_service.jobs))

    async with async_client_for(container) as client:
        response = await client.get(f"/jobs/{job_id}/history", headers=await auth_headers(client))

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["id"] == job_id
    assert payload["attempts"][0]["attempt_number"] == 1
    assert payload["events"][0]["event_type"] == "job.queued"


@pytest.mark.anyio
async def test_list_jobs_serializes_job_execution_results(mocker) -> None:
    container = make_test_container(mocker=mocker)
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

    async with async_client_for(container) as client:
        response = await client.get("/jobs", headers=await auth_headers(client))

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs"][0]["result"]["target"]["printer_name"] == "Kitchen Printer"
    assert payload["jobs"][0]["result"]["target"]["driver_key"] == "tests.fake-printers"
    assert payload["jobs"][0]["result"]["bytes_written"] == 128


def test_events_websocket_connects_successfully(mocker) -> None:
    with TestClient(create_app(container=make_test_container(mocker=mocker))) as client:
        with client.websocket_connect("/events", headers=sync_auth_headers(client)) as websocket:
            payload = websocket.receive_json()

    assert payload["kind"] == "snapshot"
    assert payload["status"]["service"]["name"] == "IoT Agent"
    assert payload["status"]["queue"]["queued"] == 1


def test_events_websocket_streams_snapshot_backed_updates(mocker) -> None:
    container = make_test_container(mocker=mocker)
    event = JobEventRecord(
        sequence=2,
        resource_id="job_123",
        event_type="job.failed",
        occurred_at=utc_now(),
        payload={"job_id": "job_123", "error_detail": "Printer offline"},
    )

    with TestClient(create_app(container=container)) as client:
        with client.websocket_connect("/events", headers=sync_auth_headers(client)) as websocket:
            websocket.receive_json()
            anyio.run(container.event_hub.publish, event)
            payload = websocket.receive_json()

    assert payload["kind"] == "event_update"
    assert payload["event"]["event_type"] == "job.failed"
    assert payload["event"]["payload"]["error_detail"] == "Printer offline"
    assert payload["status"]["queue"]["queued"] == 1


@pytest.mark.anyio
async def test_validation_errors_use_unified_problem_details_shape(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        response = await client.post("/print-jobs", json={})

    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "REQUEST_VALIDATION_FAILED"
    assert payload["type"] == "urn:iot-agent:error:request-validation-failed"
    assert payload["errors"][0]["source"]["pointer"] == "/content"


@pytest.mark.anyio
async def test_agent_errors_use_unified_problem_details_shape(mocker) -> None:
    container = make_test_container(mocker=mocker)
    container.job_service.enqueue_print_error = AgentError(
        "DEVICE_NOT_FOUND",
        "Device 'dev_missing' was not found.",
        status_code=404,
    )

    async with async_client_for(container) as client:
        response = await client.post(
            "/print-jobs",
            json={"content": {"kind": "text", "text": "Hello printer"}},
            headers=await auth_headers(client),
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "DEVICE_NOT_FOUND"
    assert payload["title"] == "Device Not Found"
    assert payload["type"] == "urn:iot-agent:error:device-not-found"


@pytest.mark.anyio
async def test_framework_http_errors_use_unified_problem_details_shape(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        response = await client.get("/missing-route")

    assert response.status_code == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "HTTP_404"
    assert payload["details"]["path"] == "/missing-route"


@pytest.mark.anyio
async def test_removed_endpoints_are_not_exposed(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        headers = await auth_headers(client)
        for method, path in (
            ("get", "/printers"),
            ("get", "/devices/printers"),
            ("post", "/printer-commands"),
            ("post", "/print"),
            ("post", "/print_receipt"),
        ):
            response = await getattr(client, method)(path, headers=headers)
            assert response.status_code == 404, path


@pytest.mark.anyio
async def test_local_token_endpoint_issues_scoped_token(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        response = await client.post(
            "/auth/local-token",
            json={
                "client_name": "tray",
                "requested_scopes": ["system:read", "events:read"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject"] == "local:tray"
    assert payload["scopes"] == ["system:read", "events:read"]


@pytest.mark.anyio
async def test_protected_routes_require_bearer_token(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        response = await client.get("/devices")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.anyio
async def test_insufficient_scope_returns_forbidden(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        headers = await auth_headers(client, requested_scopes=("system:read",))
        response = await client.get("/devices", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_SCOPE"


@pytest.mark.anyio
async def test_gateway_routes_surface_identity_and_status(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        headers = await auth_headers(client, requested_scopes=("admin:read",))
        identity = await client.get("/gateway/identity", headers=headers)
        upstream = await client.get("/gateway/upstream/status", headers=headers)

    assert identity.status_code == 200
    assert identity.json()["agent_id"] == "agt_test"
    assert upstream.status_code == 200
    assert upstream.json()["state"] == "disabled"


def test_lan_exposure_requires_tls_material() -> None:
    with pytest.raises(RuntimeError):
        create_app(
            settings=AgentSettings(
                host="0.0.0.0",
                gateway_exposure=GatewayExposure.LAN,
            )
        )


def make_test_container(
    *,
    job_kind: JobKind = JobKind.PRINT,
    operation: str = "print_job",
    command_kind: str | None = None,
    devices: tuple[DeviceRecord, ...] | None = None,
    mocker,
) -> AgentContainer:
    settings = AgentSettings(
        security_state_dir=mkdtemp(prefix="iot-agent-security-"),
        runtime_database_path=Path(mkdtemp(prefix="iot-agent-runtime-")) / "runtime.sqlite3",
    )
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
        settings=settings,
        driver_registry=DriverRegistry(drivers=()),
        printer_service=mocker.Mock(spec=StubPrinterService),
        event_hub=EventHub(),
        device_catalog=StubDeviceCatalog(devices=devices),
        job_service=StubJobService(
            jobs={job.id: job},
            queue_counts_payload={"queued": 1},
            job_attempts=(attempt,),
            job_events=(event,),
        ),
        runtime_supervisor=StubRuntimeSupervisor(),
        authorization_service=StubAuthorizationService(),
        gateway_service=StubGatewayService(settings=settings),
        application_supervisor=StubApplicationSupervisor(),
    )
