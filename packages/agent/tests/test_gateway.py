from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from inari.config import AgentSettings
from inari.db.migrations import DatabaseMigrator
from inari.gateway.connector import GatewayConnector
from inari.gateway.data_plane.base import GatewayDataPlaneTransport
from inari.gateway.enrollment import GatewayEnrollmentService
from inari.gateway.models import (
    ControllerAction,
    GatewayEnrollmentRecord,
    ManagedCertificateState,
    ManagedCertificateStatus,
    UpstreamDataPlaneKind,
    UpstreamConnectionState,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
)
from inari.gateway.protocol import (
    AgentStatusSnapshotMessage,
    ControllerCancelJobMessage,
    GatewaySnapshotPayload,
)
from inari.gateway.repositories import GatewayRepository
from inari.gateway.state_events import GatewayStateEventProjector
from inari.gateway.bridges.runtime import (
    GatewayCommandDispatcher,
    GatewayRuntimeEventForwarder,
)
from inari.runtime.events import EventHub
from inari.runtime.models import (
    JobEventRecord,
    RuntimeEventKind,
    utc_now,
)
from inari.runtime.store import RuntimeStore
from inari.security.models import GatewayMode
from inari.security.secrets import MemorySecretStore
from inari.security.state_keys import AgentStateSigningKeyService
from inari.core.version import API_VERSION, GATEWAY_PROTOCOL_VERSION
from inari.security.certificates.lifecycle import ManagedCertificateLifecycleManager
from tests.factories import StaticCertificateLifecycle, managed_certificate


@pytest.mark.anyio
async def test_connector_stays_disconnected_without_enrollment(tmp_path: Path) -> None:
    store = RuntimeStore(_database_path(tmp_path))
    DatabaseMigrator(store.database_path).ensure_current()
    connector = GatewayConnector(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_base_url="https://controller.example",
        ),
        enrollment_service=cast(
            GatewayEnrollmentService,
            FakeEnrollmentService(None),
        ),
        certificate_lifecycle_manager=cast(
            ManagedCertificateLifecycleManager,
            StaticCertificateLifecycle(managed_certificate(tmp_path / "client.pem")),
        ),
        snapshot_provider=_snapshot_provider,
        gateway_repository=GatewayRepository(store),
        state_event_projector=GatewayStateEventProjector(
            store=store,
            signing_keys=AgentStateSigningKeyService(MemorySecretStore()),
            agent_boot_id="boot_test",
        ),
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
    DatabaseMigrator(store.database_path).ensure_current()
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
        certificate_lifecycle_manager=cast(
            ManagedCertificateLifecycleManager,
            StaticCertificateLifecycle(managed_certificate(tmp_path / "client.pem")),
        ),
        snapshot_provider=_snapshot_provider,
        gateway_repository=GatewayRepository(store),
        state_event_projector=GatewayStateEventProjector(
            store=store,
            signing_keys=AgentStateSigningKeyService(MemorySecretStore()),
            agent_boot_id="boot_test",
        ),
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
@pytest.mark.parametrize("operation", ["status", "listen", "outbox", "command"])
@pytest.mark.parametrize(
    "failure", ["Rejected root pin", "Rejected certificate identity"]
)
async def test_connector_closes_transport_before_work_when_certificate_is_rejected(
    tmp_path: Path, operation: str, failure: str
) -> None:
    store = RuntimeStore(_database_path(tmp_path))
    DatabaseMigrator(store.database_path).ensure_current()
    repository = GatewayRepository(store)
    publication = AgentStatusSnapshotMessage(
        message_id="pending", snapshot=_snapshot_provider()
    )
    repository.enqueue_outbound(
        message_type=publication.type.value,
        payload=publication.model_dump(mode="json"),
    )
    lifecycle = StaticCertificateLifecycle(
        None,
        status=ManagedCertificateStatus(
            state=ManagedCertificateState.REBOOTSTRAP_REQUIRED, detail=failure
        ),
    )
    dispatcher = FakeCommandDispatcher()
    transport = FakeDataPlaneTransport()
    enrollment = _enrollment_record()
    connector = GatewayConnector(
        settings=AgentSettings(gateway_mode=GatewayMode.MANAGED),
        enrollment_service=cast(
            GatewayEnrollmentService, FakeEnrollmentService(enrollment)
        ),
        certificate_lifecycle_manager=cast(
            ManagedCertificateLifecycleManager, lifecycle
        ),
        snapshot_provider=_snapshot_provider,
        gateway_repository=repository,
        state_event_projector=GatewayStateEventProjector(
            store=store,
            signing_keys=AgentStateSigningKeyService(MemorySecretStore()),
            agent_boot_id="boot_test",
        ),
        command_dispatcher=cast(GatewayCommandDispatcher, dispatcher),
        data_plane_transport=cast(GatewayDataPlaneTransport, transport),
    )
    if operation == "status":
        await connector.sync_once()
    elif operation == "listen":
        await connector.listen_once()
    elif operation == "outbox":
        await connector.flush_outbox_once()
    else:
        await connector._handle_command(
            ControllerCancelJobMessage(
                message_id="cancel", command_id="cancel", sequence=1, job_id="job"
            ),
            session_enrollment=enrollment,
        )
    assert transport.closed
    assert transport.connections == 0
    assert not transport.status_messages
    assert not transport.publications
    assert dispatcher.cancel_calls == 0
    assert len(repository.list_pending_outbox()) == 1
    assert connector.current_status().state is UpstreamConnectionState.DISCONNECTED
    assert connector.current_status().last_error == failure


@pytest.mark.anyio
async def test_runtime_event_forwarder_enqueues_runtime_event_messages(
    tmp_path: Path,
) -> None:
    store = RuntimeStore(_database_path(tmp_path))
    DatabaseMigrator(store.database_path).ensure_current()
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
                event_type=RuntimeEventKind.JOB_SUCCEEDED,
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

    def load_enrollment(self):
        return self.record

    async def handle_auth_failure(self, enrollment) -> None:
        self.invalidations += 1


class FakeCommandDispatcher:
    def __init__(self) -> None:
        self.cancel_calls = 0

    async def handle_execute_device_command(self, message, *, enrollment) -> None:
        return None

    async def handle_cancel_job(self, message, *, enrollment) -> None:
        self.cancel_calls += 1


class FakeDataPlaneTransport:
    def __init__(self) -> None:
        self.status_messages = []
        self.publications = []
        self.closed = False
        self.connections = 0

    async def run_forever(
        self,
        *,
        enrollment,
        last_applied_controller_sequence,
        on_connected,
        on_command,
    ) -> None:
        del enrollment, last_applied_controller_sequence, on_command
        self.connections += 1
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
                "supported_device_commands": ["cut_paper"],
                "supported_controller_actions": ["jobs:cancel", "events:read"],
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
