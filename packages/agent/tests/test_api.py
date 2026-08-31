from __future__ import annotations

import anyio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, cast

import pytest
from asgi_lifespan import LifespanManager
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from inari.config import AgentSettings
from inari.application.container import AgentContainer
from inari.client_trust import (
    AcceptedDPoPProof,
    AuthorizedRequest,
    BoundOrigin,
    BusinessScope,
    ClientGrant,
    DPoPNonceRequiredError,
    EndpointPolicy,
    IssuedDPoPNonce,
    PairingScope,
    Permission,
    RequestTarget,
)
from inari.documents import (
    AdmissionAccepted,
    AdmissionRequest,
    DocumentWork,
    PreparationPrintOrigin,
)
from inari.drivers import (
    DeviceIdentity,
    DeviceKind,
    DeviceTransport,
    DriverMetadata,
    DriverRegistry,
)
from inari.core.exceptions import AgentError
from inari.gateway.models import UpstreamConnectionState, UpstreamStatus
from inari.local_api.app import create_app
from inari.local_api.device_work import DeviceWorkSubmission
from inari.local_api.print_job_queries import PrintJobQueries
from inari.local_api.header_authorization import (
    AuthorizationDecision,
    AuthorizationFailure,
    EndpointAuthorizationPolicy,
    HeaderAuthorizationRequest,
)
from inari.local_api.schemas import RuntimeEventResponse
from inari.printing.protocols import (
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
)
from inari.print_jobs import (
    PairedClientScope,
    PosPrintOrigin,
    PrintIntentPage,
    PrintIntentQuery,
    PrintJob,
    PrintJobState,
)
from inari.runtime.events import EventHub
from inari.runtime.models import (
    DeviceConnectionState,
    DeviceRecord,
    JobAttemptRecord,
    JobEventRecord,
    JobKind,
    JobRecord,
    JobState,
    RuntimeEventKind,
    utc_now,
)
from inari.security.models import (
    AccessScope,
    AgentIdentity,
    AuthenticatedPrincipal,
    GatewayExposure,
    GatewayMode,
    IssuedToken,
    PrincipalKind,
)
from inari.core.version import API_VERSION


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
class StubDatabaseMigrator:
    def ensure_current(self):
        return None


@dataclass(slots=True)
class StubDeviceCatalog:
    devices: tuple[DeviceRecord, ...]
    device_events: tuple[JobEventRecord, ...] = ()
    driver_metadata: dict[str, DriverMetadata] = field(default_factory=dict)

    def list_devices(self) -> tuple[DeviceRecord, ...]:
        return self.devices

    def get_device(self, device_id: str) -> DeviceRecord | None:
        return next((device for device in self.devices if device.id == device_id), None)

    def list_device_events(self, device_id: str, *, limit: int = 50):
        return self.device_events[:limit]

    def get_driver_metadata(
        self, *, kind: DeviceKind, driver_key: str
    ) -> DriverMetadata | None:
        metadata = self.driver_metadata.get(driver_key)
        if metadata is None or metadata.kind is not kind:
            return None
        return metadata


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
        cancelled = replace(
            job, state=JobState.CANCELLED, finished_at=utc_now(), updated_at=utc_now()
        )
        self.jobs[job_id] = cancelled
        return cancelled


@dataclass(slots=True)
class StubDocumentAdmission:
    accepted_at: datetime
    error: Exception | None = None
    submitted_request: AdmissionRequest | None = None
    submitted_work: DocumentWork | None = None

    async def admit(self, request: AdmissionRequest) -> AdmissionAccepted:
        if self.error is not None:
            raise self.error
        self.submitted_request = request
        work = request.work
        self.submitted_work = work
        return AdmissionAccepted(
            print_intent_id=work.context.print_intent_id,
            print_job_id="job_123",
            device_id=work.context.device_id,
            accepted_at=self.accepted_at,
            state_version=1,
            replayed=False,
        )


@dataclass(frozen=True, slots=True)
class StubClientTrustAuthorizer:
    authorization: AuthorizedRequest
    error: Exception | None = None

    def authorize(
        self,
        request: HeaderAuthorizationRequest,
        policy: EndpointAuthorizationPolicy,
    ) -> AuthorizationDecision:
        expected_permission = (
            Permission.JOBS_READ
            if request.path == "/v1/jobs/query"
            else Permission.RECEIPT_IMAGE
        )
        assert policy.permission is expected_permission
        if self.error is not None:
            raise self.error
        return AuthorizationDecision.authorized(self.authorization)


@dataclass(slots=True)
class StubPrintJobReader:
    page: PrintIntentPage
    query: PrintIntentQuery | None = None

    async def reconcile(self, query: PrintIntentQuery) -> PrintIntentPage:
        self.query = query
        return self.page


@dataclass(slots=True)
class StubAuthorizationService:
    issued_tokens: dict[str, AuthenticatedPrincipal] = field(default_factory=dict)

    def issue_local_token(
        self, connection, *, client_name: str, requested_scopes=None, attestation=None
    ):
        del attestation
        scopes = tuple(requested_scopes or tuple(AccessScope))
        expires_at = datetime.now(tz=UTC) + timedelta(hours=1)
        token_value = f"token::{client_name}::{len(self.issued_tokens) + 1}"
        principal = AuthenticatedPrincipal(
            subject=f"local:{client_name}",
            principal_kind=PrincipalKind.LOCAL_CLIENT,
            scopes=frozenset(scopes),
            issuer="urn:test:issuer",
            audience="inari.local",
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
            raise AgentError(
                "AUTHENTICATION_REQUIRED",
                "A bearer access token is required for this endpoint.",
                status_code=401,
            )
        _, _, token = authorization.partition(" ")
        principal = self.issued_tokens.get(token.strip())
        if principal is None:
            raise AgentError(
                "INVALID_ACCESS_TOKEN",
                "The supplied access token is invalid.",
                status_code=401,
            )
        return principal

    def require_scopes(self, principal: AuthenticatedPrincipal, scopes):
        required = tuple(scopes)
        if not principal.has_scopes(required):
            raise AgentError(
                "INSUFFICIENT_SCOPE",
                "The access token does not include the required scopes for this endpoint.",
                status_code=403,
            )
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
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


async def auth_headers(
    client: AsyncClient, *, requested_scopes: tuple[str, ...] | None = None
) -> dict[str, str]:
    payload: dict[str, object] = {"client_name": "test-client"}
    if requested_scopes is not None:
        payload["requested_scopes"] = list(requested_scopes)
    response = await client.post("/auth/local-token", json=payload)
    response.raise_for_status()
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def sync_auth_headers(
    client: TestClient, *, requested_scopes: tuple[str, ...] | None = None
) -> dict[str, str]:
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
        response = await client.get(
            "/system/status", headers=await auth_headers(client)
        )

    assert response.status_code == 200
    payload = response.json()
    device_catalog = cast(StubDeviceCatalog, container.device_catalog)
    assert payload["service"]["version"] == API_VERSION
    assert payload["devices"]["count"] == 1
    assert payload["devices"]["default_device"] == {
        "id": next(iter(device_catalog.devices)).id,
        "name": "Kitchen Printer",
    }
    assert payload["queue"]["queued"] == 1
    assert "receipt_image" in payload["supported_document_kinds"]
    assert "cut_paper" in payload["supported_device_commands"]


@pytest.mark.anyio
async def test_list_devices_uses_semantically_refined_shape(mocker) -> None:
    container = make_test_container(mocker=mocker)

    async with async_client_for(container) as client:
        response = await client.get("/devices", headers=await auth_headers(client))

    assert response.status_code == 200
    payload = response.json()
    device_catalog = cast(StubDeviceCatalog, container.device_catalog)
    assert "ok" not in payload
    assert payload["summary"]["default_device"] == {
        "id": next(iter(device_catalog.devices)).id,
        "name": "Kitchen Printer",
    }
    device = payload["devices"][0]
    assert device["driver_key"] == "tests.fake-printers"
    assert device["driver"] == {
        "key": "tests.fake-printers",
        "display_name": "Test Printer Driver",
        "kind": "printer",
        "platform": "test",
    }
    assert device["device_class"] == "physical"
    assert device["connection"]["state"] == "online"
    assert "observed_at" in device["connection"]
    assert device["printer"]["supported_transports"] == ["raw", "text", "document"]
    assert device["printer"]["capabilities"] == ["cash_drawer"]
    assert device["metadata"]["source"] == "tests"


@pytest.mark.anyio
async def test_list_devices_marks_virtual_windows_printers(mocker) -> None:
    device = DeviceRecord.from_printer(
        PrinterDevice(
            name="Microsoft Print to PDF",
            driver_key="windows.printers",
            identity=DeviceIdentity(
                transport=DeviceTransport.SPOOLER,
                os_instance_id="windows-queue:Microsoft Print to PDF",
            ),
            capabilities=PrinterCapabilities(
                raw=False, text=True, documents=True, cash_drawer=False
            ),
        ),
        connection_state=DeviceConnectionState.ONLINE,
    )

    async with async_client_for(
        make_test_container(devices=(device,), mocker=mocker)
    ) as client:
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


def test_committed_event_fixture_matches_the_python_contract() -> None:
    fixture = (
        Path(__file__).resolve().parents[3] / "contracts" / "local-agent.events.json"
    )
    envelopes = json.loads(fixture.read_text(encoding="utf-8"))

    events = [
        RuntimeEventResponse.model_validate(envelope["event"]) for envelope in envelopes
    ]

    assert all(envelope["kind"] == "event_update" for envelope in envelopes)
    assert {event.event_type for event in events} == set(RuntimeEventKind)
    assert events[0].resource_id == "dev_front_desk"


def device_work_envelope(
    device_id: str,
    *,
    print_intent_id: str = "pi_v1_test",
    origin: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "contract_major": 1,
        "operation": "receipt_image",
        "media_type": "image/jpeg",
        "context": {
            "contract_major": 1,
            "print_intent_id": print_intent_id,
            "origin_submission_key": f"osk:{print_intent_id}",
            "origin": origin
            or {
                "kind": "pos",
                "pos_session_id": "pos_session_42",
                "offline_order_id": "order-1",
                "server_order_id": None,
                "document_kind": "customer_receipt",
                "content_revision": "revision-1",
            },
            "binding_revision_id": "binding_revision_9",
            "device_id": device_id,
            "copy_ordinal": 1,
        },
    }


@pytest.mark.anyio
async def test_submit_device_work_returns_accepted_print_job(mocker) -> None:
    container = make_test_container(mocker=mocker)
    device_id = next(iter(cast(StubDeviceCatalog, container.device_catalog).devices)).id
    envelope = device_work_envelope(device_id)

    async with async_client_for(container) as client:
        headers = await auth_headers(client)
        headers["Idempotency-Key"] = "pi_v1_test"
        response = await client.post(
            "/v1/device-work",
            files={
                "envelope": (
                    None,
                    json.dumps(envelope, separators=(",", ":"), sort_keys=True),
                    "application/json",
                ),
                "document": (
                    "receipt.jpg",
                    b"\xff\xd8receipt\xff\xd9",
                    "image/jpeg",
                ),
            },
            headers=headers,
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["ok"] is True
    assert payload["state"] == "accepted"
    assert payload["print_job_id"] == "job_123"
    admission = cast(StubDocumentAdmission, container.document_admission)
    assert admission.submitted_work is not None
    assert admission.submitted_request is not None
    assert admission.submitted_work.idempotency_key == "pi_v1_test"
    assert admission.submitted_work.context.device_id == device_id
    assert admission.submitted_work.context.organization_id == "org_1"
    assert admission.submitted_work.context.site_id == "site_1"
    assert admission.submitted_work.context.paired_client_id == "pairing_1"
    assert admission.submitted_work.context.actor_id == "res.users:7"
    assert admission.submitted_request.grant.grant_id == "grant_1"
    assert admission.submitted_request.grant.generation == 1
    assert (
        admission.submitted_request.grant.authorization_digest
        == "authorization_digest_1"
    )


@pytest.mark.anyio
async def test_submit_preparation_work_preserves_segment_identity(mocker) -> None:
    container = make_test_container(mocker=mocker)
    device_id = next(iter(cast(StubDeviceCatalog, container.device_catalog).devices)).id
    envelope = device_work_envelope(
        device_id,
        print_intent_id="pi_v1_preparation",
        origin={
            "kind": "preparation",
            "pos_session_id": "pos_session_42",
            "offline_order_id": "order-1",
            "server_order_id": None,
            "document_kind": "preparation_ticket",
            "content_revision": "sha256:ticket-image",
            "segment_kind": "new",
            "segment_index": 0,
            "preparation_revision": "sha256:order-change",
        },
    )

    async with async_client_for(container) as client:
        headers = await auth_headers(client)
        headers["Idempotency-Key"] = "pi_v1_preparation"
        response = await client.post(
            "/v1/device-work",
            files={
                "envelope": (
                    None,
                    json.dumps(envelope, separators=(",", ":"), sort_keys=True),
                    "application/json",
                ),
                "document": ("ticket.jpg", b"\xff\xd8ticket\xff\xd9", "image/jpeg"),
            },
            headers=headers,
        )

    assert response.status_code == 202
    admission = cast(StubDocumentAdmission, container.document_admission)
    assert admission.submitted_work is not None
    origin = admission.submitted_work.context.origin
    assert isinstance(origin, PreparationPrintOrigin)
    assert origin.segment_kind == "new"
    assert origin.segment_index == 0
    assert origin.preparation_revision == "sha256:order-change"


@pytest.mark.anyio
async def test_query_print_jobs_returns_scoped_public_projection(mocker) -> None:
    container = make_test_container(mocker=mocker)
    queries = container.print_job_queries
    assert isinstance(queries.reader, StubPrintJobReader)
    now = datetime(2026, 8, 31, 12, tzinfo=UTC)
    queries.reader.page = PrintIntentPage(
        jobs=(
            PrintJob(
                job_id="job_123",
                intent_id="intent_123",
                device_id="device_123",
                origin=PosPrintOrigin(
                    organization_id="org_1",
                    site_id="site_1",
                    database="odoo",
                    paired_client_id="pairing_1",
                    pos_configuration_id="pos_config_7",
                    pos_session_id="session_1",
                    offline_order_id="order_1",
                    server_order_id=None,
                    document_kind="customer_receipt",
                    content_revision="sha256:receipt",
                ),
                state=PrintJobState.ACCEPTED,
                state_version=1,
                accepted_at=now,
                expires_at=now + timedelta(minutes=5),
                retryable=False,
                contract_version="v1",
            ),
        ),
        missing_print_intent_ids=("intent_missing",),
        high_water_mark=17,
    )

    async with async_client_for(container) as client:
        response = await client.post(
            "/v1/jobs/query",
            json={"print_intent_ids": ["intent_123", "intent_missing"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs"][0]["print_job_id"] == "job_123"
    assert payload["jobs"][0]["origin"]["kind"] == "pos"
    assert payload["missing_print_intent_ids"] == ["intent_missing"]
    assert payload["high_water_mark"] == 17
    assert queries.reader.query is not None
    assert queries.reader.query.scope == PairedClientScope(
        organization_id="org_1",
        site_id="site_1",
        pos_configuration_id="pos_config_7",
        paired_client_id="pairing_1",
    )


@pytest.mark.anyio
async def test_query_print_jobs_rejects_unbounded_input_before_read(mocker) -> None:
    container = make_test_container(mocker=mocker)
    queries = container.print_job_queries
    assert isinstance(queries.reader, StubPrintJobReader)

    async with async_client_for(container) as client:
        response = await client.post(
            "/v1/jobs/query",
            json={"print_intent_ids": [f"intent_{index}" for index in range(101)]},
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "payload_invalid"
    assert queries.reader.query is None


@pytest.mark.anyio
async def test_query_print_jobs_requires_jobs_read_permission(mocker) -> None:
    container = make_test_container(mocker=mocker)
    authorization = authorized_device_work()
    limited = replace(
        authorization,
        grant=replace(
            authorization.grant,
            permissions=frozenset({Permission.RECEIPT_IMAGE}),
        ),
    )
    container = replace(
        container,
        device_work_authorizer=StubClientTrustAuthorizer(limited),
    )

    async with async_client_for(container) as client:
        response = await client.post(
            "/v1/jobs/query",
            json={"print_intent_ids": ["intent_123"]},
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "permission_denied"


@pytest.mark.anyio
async def test_device_work_trust_failure_keeps_browser_cors_headers(mocker) -> None:
    container = make_test_container(mocker=mocker)
    authorization = authorized_device_work()
    container = replace(
        container,
        device_work_authorizer=StubClientTrustAuthorizer(
            authorization,
            error=AuthorizationFailure(
                "missing_client_grant",
                "The request needs a Client Grant.",
            ),
        ),
    )

    async with async_client_for(container) as client:
        response = await client.post(
            "/v1/device-work",
            content=b"body-must-not-be-read",
            headers={"Origin": "http://127.0.0.1:8069"},
        )

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == ("http://127.0.0.1:8069")
    assert response.headers["access-control-expose-headers"] == (
        "DPoP-Nonce, Date, X-Correlation-ID"
    )


@pytest.mark.anyio
async def test_device_work_returns_browser_visible_dpop_nonce_challenge(mocker) -> None:
    container = make_test_container(mocker=mocker)
    authorization = authorized_device_work()
    issued_at = datetime(2026, 8, 30, 12, tzinfo=UTC)
    container = replace(
        container,
        device_work_authorizer=StubClientTrustAuthorizer(
            authorization,
            error=DPoPNonceRequiredError(
                IssuedDPoPNonce(
                    nonce="nonce_1234567890",
                    issued_at=issued_at,
                    expires_at=issued_at + timedelta(minutes=2),
                )
            ),
        ),
    )

    async with async_client_for(container) as client:
        response = await client.post(
            "/v1/device-work",
            content=b"body-must-not-be-read",
            headers={"Origin": "http://127.0.0.1:8069"},
        )

    assert response.status_code == 401
    assert response.headers["dpop-nonce"] == "nonce_1234567890"
    assert response.headers["www-authenticate"] == (
        'DPoP realm="inari", error="use_dpop_nonce"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == ("http://127.0.0.1:8069")


@pytest.mark.anyio
async def test_submit_device_command_returns_queued_job_resource(mocker) -> None:
    container = make_test_container(
        job_kind=JobKind.COMMAND,
        operation="cut_paper",
        command_kind="cut_paper",
        mocker=mocker,
    )

    async with async_client_for(container) as client:
        response = await client.post(
            "/device-commands",
            json={
                "target": {
                    "device_id": next(
                        iter(cast(StubDeviceCatalog, container.device_catalog).devices)
                    ).id
                },
                "command": {"kind": "cut_paper", "mode": "full"},
            },
            headers=await auth_headers(client),
        )

    assert response.status_code == 202
    payload = response.json()
    job_service = cast(StubJobService, container.job_service)
    assert payload["job"]["kind"] == "device_command"
    assert payload["job"]["operation"] == "cut_paper"
    assert job_service.submitted_command_operation is not None
    assert (
        cast(Any, job_service.submitted_command_operation).command.kind == "cut_paper"
    )


@pytest.mark.anyio
async def test_job_history_response_includes_attempts_and_events(mocker) -> None:
    container = make_test_container(mocker=mocker)
    job_service = cast(StubJobService, container.job_service)
    job_id = next(iter(job_service.jobs))

    async with async_client_for(container) as client:
        response = await client.get(
            f"/jobs/{job_id}/history", headers=await auth_headers(client)
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["id"] == job_id
    assert payload["attempts"][0]["attempt_number"] == 1
    assert payload["events"][0]["event_type"] == "job.queued"


@pytest.mark.anyio
async def test_list_jobs_serializes_job_execution_results(mocker) -> None:
    container = make_test_container(mocker=mocker)
    job_service = cast(StubJobService, container.job_service)
    job_id = next(iter(job_service.jobs))
    completed = replace(
        job_service.jobs[job_id],
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
    job_service.jobs[job_id] = completed

    async with async_client_for(container) as client:
        response = await client.get("/jobs", headers=await auth_headers(client))

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs"][0]["result"]["target"]["printer_name"] == "Kitchen Printer"
    assert payload["jobs"][0]["result"]["target"]["driver_key"] == "tests.fake-printers"
    assert payload["jobs"][0]["result"]["bytes_written"] == 128


def test_events_websocket_connects_successfully(mocker) -> None:
    with TestClient(create_app(container=make_test_container(mocker=mocker))) as client:
        with client.websocket_connect(
            "/events", headers=sync_auth_headers(client)
        ) as websocket:
            payload = websocket.receive_json()

    assert payload["kind"] == "snapshot"
    assert payload["status"]["service"]["name"] == "Inari"
    assert payload["status"]["queue"]["queued"] == 1


def test_events_websocket_streams_snapshot_backed_updates(mocker) -> None:
    container = make_test_container(mocker=mocker)
    event = JobEventRecord(
        sequence=2,
        resource_id="job_123",
        event_type=RuntimeEventKind.JOB_FAILED,
        occurred_at=utc_now(),
        payload={"job_id": "job_123", "error_detail": "Printer offline"},
    )

    with TestClient(create_app(container=container)) as client:
        with client.websocket_connect(
            "/events", headers=sync_auth_headers(client)
        ) as websocket:
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
        response = await client.post("/v1/device-work")

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == "request_malformed"
    assert payload["type"] == "urn:inari:problem:v1:request_malformed"
    assert payload["status"] == 400


@pytest.mark.anyio
async def test_agent_errors_use_unified_problem_details_shape(mocker) -> None:
    container = make_test_container(mocker=mocker)
    cast(StubDocumentAdmission, container.document_admission).error = AgentError(
        "DEVICE_NOT_FOUND",
        "Device 'dev_missing' was not found.",
        status_code=404,
    )
    device_id = next(iter(cast(StubDeviceCatalog, container.device_catalog).devices)).id
    envelope = device_work_envelope(device_id, print_intent_id="pi_v1_missing")

    async with async_client_for(container) as client:
        headers = await auth_headers(client)
        headers["Idempotency-Key"] = "pi_v1_missing"
        response = await client.post(
            "/v1/device-work",
            files={
                "envelope": (
                    None,
                    json.dumps(envelope, separators=(",", ":"), sort_keys=True),
                    "application/json",
                ),
                "document": (
                    "receipt.jpg",
                    b"\xff\xd8receipt\xff\xd9",
                    "image/jpeg",
                ),
            },
            headers=headers,
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error_code"] == "resource_not_found"
    assert payload["title"] == "Resource not found"
    assert payload["type"] == "urn:inari:problem:v1:resource_not_found"


@pytest.mark.anyio
async def test_framework_http_errors_use_unified_problem_details_shape(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        response = await client.get("/missing-route")

    assert response.status_code == 404
    payload = response.json()
    assert payload["error_code"] == "resource_not_found"
    assert payload["status"] == 404


@pytest.mark.anyio
async def test_removed_endpoints_are_not_exposed(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        headers = await auth_headers(client)
        for method, path in (
            ("get", "/printers"),
            ("get", "/devices/printers"),
            ("post", "/printer-commands"),
            ("post", "/print-jobs"),
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
    assert response.json()["error_code"] == "trust_required"


@pytest.mark.anyio
async def test_insufficient_scope_returns_forbidden(mocker) -> None:
    async with async_client_for(make_test_container(mocker=mocker)) as client:
        headers = await auth_headers(client, requested_scopes=("system:read",))
        response = await client.get("/devices", headers=headers)

    assert response.status_code == 403
    assert response.json()["error_code"] == "permission_denied"


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


def authorized_device_work() -> AuthorizedRequest:
    now = datetime.now(tz=UTC)
    browser_origin = BoundOrigin("https://odoo.example")
    agent_endpoint = BoundOrigin("https://agent.example")
    business = BusinessScope(
        database="odoo",
        company_id="company_1",
        organization_id="org_1",
        site_id="site_1",
        pos_configuration_id="pos_config_7",
    )
    scope = PairingScope(
        agent_id="agent_1",
        browser_origin=browser_origin,
        agent_endpoint=agent_endpoint,
        business=business,
        audience="inari.local",
    )
    target = RequestTarget("POST", "https://agent.example/v1/device-work")
    grant = ClientGrant(
        grant_id="grant_1",
        pairing_id="pairing_1",
        jwk_thumbprint="thumbprint_1",
        scope=scope,
        actor_id="res.users:7",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE, Permission.JOBS_READ}),
        authorization_digest="authorization_digest_1",
        generation=1,
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=14),
    )
    proof = AcceptedDPoPProof(
        jwk_thumbprint=grant.jwk_thumbprint,
        htm=target.method,
        htu=target.uri,
        iat=now,
        ath="access_hash_1",
        nonce="nonce_value_1",
        jti="proof_value_1",
        accepted_at=now,
    )
    endpoint = EndpointPolicy(
        agent_id=scope.agent_id,
        audience=scope.audience,
        browser_origin=browser_origin,
        agent_endpoint=agent_endpoint,
        business=business,
        allowed_methods=frozenset({"POST"}),
        allowed_paths=("/v1/device-work",),
    )
    return AuthorizedRequest(
        target=target,
        grant=grant,
        dpop=proof,
        endpoint=endpoint,
        accepted_at=now,
    )


def make_test_container(
    *,
    job_kind: JobKind = JobKind.COMMAND,
    operation: str = "cut_paper",
    command_kind: str | None = "cut_paper",
    devices: tuple[DeviceRecord, ...] | None = None,
    mocker,
) -> AgentContainer:
    settings = AgentSettings(
        security_state_dir=Path(mkdtemp(prefix="inari-security-")),
        runtime_database_path=Path(mkdtemp(prefix="inari-runtime-"))
        / "runtime.sqlite3",
    )
    default_device = DeviceRecord.from_printer(
        PrinterDevice(
            name="Kitchen Printer",
            driver_key="tests.fake-printers",
            identity=DeviceIdentity(
                transport=DeviceTransport.SPOOLER,
                os_instance_id="test-queue:kitchen",
            ),
            is_default=True,
            preferred_transport=PrinterTransport.RAW,
            capabilities=PrinterCapabilities(
                raw=True, text=True, documents=True, cash_drawer=True
            ),
            metadata={"source": "tests"},
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
        request_payload={"command": {"kind": "cut_paper", "mode": "full"}},
        request_metadata={"source": "test"},
        content_kind=None,
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
        event_type=RuntimeEventKind.JOB_QUEUED,
        occurred_at=now,
        payload={"job_id": job.id},
    )
    device_catalog = StubDeviceCatalog(
        devices=devices,
        driver_metadata={
            "tests.fake-printers": DriverMetadata(
                key="tests.fake-printers",
                display_name="Test Printer Driver",
                kind=DeviceKind.PRINTER,
                platform="test",
            )
        },
    )
    job_service = StubJobService(
        jobs={job.id: job},
        queue_counts_payload={"queued": 1},
        job_attempts=(attempt,),
        job_events=(event,),
    )
    admission = StubDocumentAdmission(accepted_at=now)
    authorization = authorized_device_work()
    print_job_reader = StubPrintJobReader(
        PrintIntentPage(jobs=(), missing_print_intent_ids=(), high_water_mark=0)
    )
    return AgentContainer(
        settings=settings,
        database_migrator=cast(Any, StubDatabaseMigrator()),
        driver_registry=DriverRegistry(drivers=()),
        printer_service=cast(Any, mocker.Mock(spec=StubPrinterService)),
        event_hub=EventHub(),
        device_catalog=cast(Any, device_catalog),
        job_service=cast(Any, job_service),
        runtime_supervisor=cast(Any, StubRuntimeSupervisor()),
        document_admission=cast(Any, admission),
        device_work_submission=DeviceWorkSubmission(admission=admission),
        print_job_queries=PrintJobQueries(reader=print_job_reader),
        physical_execution=cast(Any, object()),
        device_work_authorizer=StubClientTrustAuthorizer(authorization),
        authorization_service=cast(Any, StubAuthorizationService()),
        gateway_service=cast(Any, StubGatewayService(settings=settings)),
        application_supervisor=cast(Any, StubApplicationSupervisor()),
    )
