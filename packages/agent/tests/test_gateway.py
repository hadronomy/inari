from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from inari.config import AgentSettings
from inari.gateway.connector import GatewayConnector
from inari.gateway.data_plane.base import GatewayDataPlaneTransport
from inari.gateway.enrollment import GatewayEnrollmentService
from inari.gateway.models import (
    ControllerAction,
    GatewayEnrollmentRecord,
    UpstreamDataPlaneKind,
    UpstreamConnectionState,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
)
from inari.gateway.protocol import AgentStatusSnapshotMessage, GatewaySnapshotPayload
from inari.gateway.repositories import GatewayRepository
from inari.gateway.runtime_bridge import (
    GatewayCommandDispatcher,
    GatewayRuntimeEventForwarder,
)
from inari.printers import PrinterCapabilities, PrinterDevice, PrinterTransport
from inari.runtime.events import EventHub
from inari.runtime.models import (
    DeviceConnectionState,
    DeviceRecord,
    JobEventRecord,
    JobKind,
    JobRecord,
    JobState,
    utc_now,
)
from inari.runtime.services import JobService
from inari.runtime.store import RuntimeStore
from inari.security.models import GatewayMode
from inari.version import API_VERSION, GATEWAY_PROTOCOL_VERSION


@pytest.mark.anyio
async def test_connector_stays_disconnected_without_enrollment(tmp_path: Path) -> None:
    store = RuntimeStore(_database_path(tmp_path))
    store.initialize()
    connector = GatewayConnector(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_base_url="https://controller.example",
        ),
        enrollment_service=cast(
            GatewayEnrollmentService,
            FakeEnrollmentService(None),
        ),
        certificate_lifecycle_manager=None,
        snapshot_provider=_snapshot_provider,
        gateway_repository=GatewayRepository(store),
        command_dispatcher=cast(
            GatewayCommandDispatcher,
            FakeCommandDispatcher(),
        ),
        data_plane_transport=cast(
            GatewayDataPlaneTransport,
            FakeDataPlaneTransport(),
        ),
    )

    await connector.sync_once()

    assert connector.current_status().state is UpstreamConnectionState.DISCONNECTED


@pytest.mark.anyio
async def test_connector_marks_online_after_successful_status_sync(
    tmp_path: Path,
) -> None:
    enrollment = _enrollment_record(
        controller_name="Controller",
        controller_instance_id="controller-1",
    )
    store = RuntimeStore(_database_path(tmp_path))
    store.initialize()
    transport = FakeDataPlaneTransport()
    connector = GatewayConnector(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_base_url="https://controller.example",
        ),
        enrollment_service=cast(
            GatewayEnrollmentService,
            FakeEnrollmentService(enrollment),
        ),
        certificate_lifecycle_manager=None,
        snapshot_provider=_snapshot_provider,
        gateway_repository=GatewayRepository(store),
        command_dispatcher=cast(
            GatewayCommandDispatcher,
            FakeCommandDispatcher(),
        ),
        data_plane_transport=cast(GatewayDataPlaneTransport, transport),
    )

    await connector.sync_once()

    assert connector.current_status().state is UpstreamConnectionState.ONLINE
    assert len(transport.status_messages) == 1
    assert isinstance(transport.status_messages[0], AgentStatusSnapshotMessage)
    assert connector.current_status().controller_name == "Controller"


@pytest.mark.anyio
async def test_dispatcher_accepts_remote_print_job_and_persists_response(
    tmp_path: Path,
) -> None:
    store = RuntimeStore(_database_path(tmp_path))
    store.initialize()
    repository = GatewayRepository(store)
    dispatcher = GatewayCommandDispatcher(
        job_service=cast(JobService, StubJobService()),
        gateway_repository=repository,
    )
    enrollment = _enrollment_record(
        controller_actions=(ControllerAction.JOBS_CREATE,),
    )
    from inari.gateway.protocol import ControllerSubmitPrintJobMessage

    message = ControllerSubmitPrintJobMessage.model_validate(
        {
            "type": "controller.command.submit_print_job",
            "message_id": "msg_1",
            "command_id": "cmd_1",
            "sequence": 1,
            "payload": {
                "content": {"kind": "text", "text": "Hello gateway"},
                "target": {"printer_name": "Kitchen Printer"},
            },
        }
    )

    await dispatcher.handle_submit_print_job(message, enrollment=enrollment)

    record = repository.get_inbound_command("cmd_1")
    assert record is not None
    assert record.state.value == "accepted"
    outbox = repository.list_pending_outbox()
    assert len(outbox) == 1
    assert outbox[0].message_type == "agent.command.accepted"


@pytest.mark.anyio
async def test_runtime_event_forwarder_enqueues_runtime_event_messages(
    tmp_path: Path,
) -> None:
    store = RuntimeStore(_database_path(tmp_path))
    store.initialize()
    repository = GatewayRepository(store)
    event_hub = EventHub()
    forwarder = GatewayRuntimeEventForwarder(
        event_hub=event_hub,
        gateway_repository=repository,
    )
    worker = asyncio.create_task(forwarder.run_forever())
    try:
        await asyncio.sleep(0)
        await event_hub.publish(
            JobEventRecord(
                sequence=7,
                resource_id="job_123",
                event_type="job.succeeded",
                occurred_at=utc_now(),
                payload={"job_id": "job_123"},
            )
        )
        await asyncio.sleep(0)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    outbox = repository.list_pending_outbox()
    assert len(outbox) == 1
    assert outbox[0].message_type == "agent.runtime.event"


class FakeEnrollmentService:
    def __init__(self, record: GatewayEnrollmentRecord | None) -> None:
        self.record = record
        self.invalidations = 0
        self.certificate_service = _NullCertificateService()

    async def ensure_enrolled(self):
        return self.record

    async def handle_auth_failure(self, enrollment) -> None:
        self.invalidations += 1


class FakeCommandDispatcher:
    async def handle_submit_print_job(self, message, *, enrollment) -> None:
        return None

    async def handle_execute_device_command(self, message, *, enrollment) -> None:
        return None

    async def handle_cancel_job(self, message, *, enrollment) -> None:
        return None


class FakeDataPlaneTransport:
    def __init__(self) -> None:
        self.status_messages = []
        self.publications = []
        self.closed = False

    async def run_forever(
        self,
        *,
        enrollment,
        last_applied_controller_sequence,
        on_connected,
        on_command,
    ) -> None:
        del enrollment, last_applied_controller_sequence, on_command
        await on_connected()
        return None

    async def publish_status(self, *, enrollment, message) -> None:
        del enrollment
        self.status_messages.append(message)

    async def publish_publications(self, *, enrollment, messages) -> None:
        del enrollment
        self.publications.extend(messages)

    async def close(self) -> None:
        self.closed = True


class _NullCertificateService:
    def current_certificate(self) -> None:
        return None


class StubJobService:
    def __init__(self) -> None:
        device = DeviceRecord.from_printer(
            PrinterDevice(
                name="Kitchen Printer",
                driver_key="tests.fake-printers",
                is_default=True,
                preferred_transport=PrinterTransport.RAW,
                capabilities=PrinterCapabilities(
                    raw=True, text=True, documents=True, cash_drawer=True
                ),
            ),
            connection_state=DeviceConnectionState.ONLINE,
        )
        now = utc_now()
        self.job = JobRecord(
            id="job_remote_1",
            kind=JobKind.PRINT,
            operation="print_job",
            device_id=device.id,
            device_kind=device.kind,
            device_name=device.name,
            state=JobState.QUEUED,
            request_payload={"content": {"kind": "text", "text": "Hello gateway"}},
            request_metadata={"source": "remote"},
            content_kind="text",
            command_kind=None,
            attempt_count=0,
            max_attempts=3,
            created_at=now,
            updated_at=now,
            queued_at=now,
            next_run_at=now + timedelta(seconds=1),
        )

    async def enqueue_print(self, operation):
        return self.job

    async def enqueue_command(self, operation):
        return self.job

    async def cancel(self, job_id: str) -> JobRecord:
        return self.job


def _database_path(temp_dir: Path) -> Path:
    return temp_dir / "runtime.sqlite3"


def _snapshot_provider() -> GatewaySnapshotPayload:
    return GatewaySnapshotPayload.model_validate(
        {
        "generated_at": utc_now().isoformat(),
        "protocol": {
            "version": GATEWAY_PROTOCOL_VERSION,
            "supported_versions": [GATEWAY_PROTOCOL_VERSION],
        },
        "service": {"name": "Inari", "version": API_VERSION},
        "security": {
            "mode": "managed",
            "exposure": "loopback",
            "tls_required": False,
            "edge_provider": "direct",
            "certificate_mode": "controller",
            "mutual_tls_mode": "disabled",
            "mutual_tls_enabled": False,
            "certificate_expires_at": None,
        },
        "runtime": {
            "queue": {"total": 0},
            "devices": {
                "count": 0,
                "online_count": 0,
                "offline_count": 0,
                "kind_counts": {},
                "default_device_id": None,
                "default_device_name": None,
            },
        },
        "capabilities": {
            "supported_content_kinds": ["text"],
            "supported_device_commands": ["cut_paper"],
            "supported_controller_actions": ["jobs:create", "events:read"],
            "features": ["status_publication", "zenoh_data_plane"],
            "transport": "https+zenoh",
            "client_certificate_present": False,
        },
        "observability": {},
        }
    )


def _enrollment_record(
    *,
    controller_actions: tuple[ControllerAction, ...] = (),
    controller_name: str | None = None,
    controller_instance_id: str | None = None,
) -> GatewayEnrollmentRecord:
    return GatewayEnrollmentRecord(
        enrolled_at=utc_now(),
        data_plane=ZenohDataPlaneConfig(
            kind=UpstreamDataPlaneKind.ZENOH,
            session_mode=ZenohSessionMode.CLIENT,
            connect_endpoints=("tls/router.example.com:7447",),
            namespace="iot/v1/agents/agt_test",
            serialization=ZenohSerialization.JSON,
            auth_kind=ZenohDataPlaneAuthKind.MTLS,
            close_link_on_expiration=True,
        ),
        controller_actions=controller_actions,
        protocol_version=GATEWAY_PROTOCOL_VERSION,
        controller_name=controller_name,
        controller_instance_id=controller_instance_id,
    )
