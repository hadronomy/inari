from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
import zenoh
from alembic import command

from inari.config import AgentSettings
from inari.core.exceptions import AgentError
from inari.db.migrations import DatabaseMigrator
from inari.gateway.connector import GatewayConnector
from inari.gateway.data_plane.zenoh import ZenohGatewayTransport
from inari.gateway.protocol import AgentRuntimeEventMessage
from inari.gateway.repositories import GatewayRepository
from inari.runtime.store import RuntimeStore
from inari.security.certificates.lifecycle import ManagedCertificateLifecycleManager
from inari.security.certificates.store import CertificateLifecycleService
from inari.security.models import GatewayMode
from tests.factories import (
    StaticCertificateLifecycle,
    enrollment_record,
    managed_certificate,
)


def observation() -> AgentRuntimeEventMessage:
    return AgentRuntimeEventMessage.model_validate(
        {
            "type": "agent.runtime.event",
            "message_id": "ase_test",
            "occurred_at": "2026-09-06T12:00:00Z",
            "command_id": "dispatch_test",
            "job_id": "job_test",
            "event": {
                "sequence": 1,
                "resource_kind": "print_job",
                "resource_id": "job_test",
                "event_type": "print_job.accepted",
                "occurred_at": "2026-09-06T12:00:00Z",
                "payload": {"state_envelope": "header.payload.signature"},
            },
        }
    )


def receipt() -> dict[str, object]:
    return {
        "contract_major": 1,
        "message_id": "ase_test",
        "state_envelope_sha256": hashlib.sha256(
            b"header.payload.signature"
        ).hexdigest(),
    }


class Session:
    def __init__(self) -> None:
        self.reply: dict[str, object] | None = receipt()
        self.reply_key = "iot/v1/agents/agt_test/state/commit"
        self.requests: list[tuple[str, str]] = []

    def get(self, key: str, *, payload: str, **kwargs):
        assert kwargs["encoding"] == zenoh.Encoding.APPLICATION_JSON
        self.requests.append((key, payload))
        if self.reply is None:
            return []
        return [
            SimpleNamespace(
                ok=SimpleNamespace(
                    key_expr=self.reply_key,
                    payload=zenoh.ZBytes(json.dumps(self.reply)),
                )
            )
        ]

    def declare_subscriber(self, *args):
        return Mock()

    def liveliness(self):
        return Mock()

    def close(self):
        pass


def transport(tmp_path: Path, session: Session) -> ZenohGatewayTransport:
    certificate = tmp_path / "certificate.pem"
    key = tmp_path / "key.pem"
    certificate.touch()
    key.touch()
    return ZenohGatewayTransport(
        settings=AgentSettings(),
        certificate_service=CertificateLifecycleService(
            certificate_path=certificate,
            private_key_path=key,
        ),
        session_open=lambda config: session,
        config_factory=Mock,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "message_id",
        "digest",
        "contract",
        "boolean_contract",
        "key",
        "extra",
    ],
)
async def test_state_delivery_requires_the_matching_storage_receipt(
    tmp_path: Path,
    invalid: str,
) -> None:
    session = Session()
    expected = receipt()
    match invalid:
        case "missing":
            session.reply = None
        case "message_id":
            expected["message_id"] = "ase_other"
        case "digest":
            expected["state_envelope_sha256"] = "0" * 64
        case "contract":
            expected["contract_major"] = 2
        case "boolean_contract":
            expected["contract_major"] = True
        case "key":
            session.reply_key = "iot/v1/agents/agt_other/state/commit"
        case "extra":
            expected["unsigned"] = "value"
    if invalid != "missing":
        session.reply = expected
    client = transport(tmp_path, session)
    with pytest.raises(AgentError, match="not confirmed storage"):
        await client.publish_publications(
            enrollment=enrollment_record(), messages=(observation(),)
        )
    assert len(session.requests) == 1
    await client.close()


@pytest.mark.anyio
async def test_lost_receipt_keeps_the_same_observation_pending_until_retry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(database_path).ensure_current()
    repository = GatewayRepository(RuntimeStore(database_path))
    message = observation()
    repository.enqueue_outbound(
        message_type=message.type, payload=message.model_dump(mode="json")
    )
    session = Session()
    session.reply = None
    client = transport(tmp_path, session)
    enrollment_service = Mock()
    enrollment_service.ensure_enrolled = AsyncMock(return_value=enrollment_record())
    connector = GatewayConnector(
        settings=AgentSettings(gateway_mode=GatewayMode.MANAGED),
        enrollment_service=enrollment_service,
        certificate_lifecycle_manager=cast(
            ManagedCertificateLifecycleManager,
            StaticCertificateLifecycle(managed_certificate(tmp_path / "certificate.pem")),
        ),
        snapshot_provider=Mock(),
        sharing_policy=Mock(),
        gateway_repository=repository,
        command_dispatcher=Mock(),
        state_event_projector=Mock(),
        data_plane_transport=client,
    )
    with pytest.raises(AgentError, match="not confirmed storage"):
        await connector.flush_outbox_once()
    assert [row.message_id for row in repository.list_pending_outbox()] == [
        message.message_id
    ]
    session.reply = receipt()
    await connector.flush_outbox_once()
    assert repository.list_pending_outbox() == ()
    assert len(session.requests) == 2
    assert session.requests[0] == session.requests[1]
    await connector.flush_outbox_once()
    assert len(session.requests) == 2
    await connector.close()


def test_upgrade_requeues_sent_evidence_without_replacing_its_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical.sqlite3"
    migrator = DatabaseMigrator(path)
    command.upgrade(migrator._build_alembic_config(), "20260906_0015")
    repository = GatewayRepository(RuntimeStore(path))
    message = observation()
    repository.enqueue_outbound(
        message_type=message.type, payload=message.model_dump(mode="json")
    )
    repository.mark_outbox_sent(message.message_id)
    repository.enqueue_outbound(
        message_type="agent.error", payload={"message_id": "other"}
    )
    repository.mark_outbox_sent("other")
    migrator.ensure_current()
    pending = repository.list_pending_outbox()
    assert len(pending) == 1
    assert pending[0].message_id == message.message_id
    assert pending[0].payload == message.model_dump(mode="json")
    assert pending[0].sent_at is None
    repository.mark_outbox_sent(message.message_id)
    migrator.ensure_current()
    assert repository.list_pending_outbox() == ()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT state FROM gateway_outbox WHERE message_id = 'other'"
        ).fetchone() == ("sent",)


def test_failed_observation_does_not_hold_later_observations_behind_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()
    repository = GatewayRepository(RuntimeStore(path))
    for identity in ("ase_first", "ase_second"):
        message = observation().model_copy(update={"message_id": identity})
        repository.enqueue_outbound(
            message_type=message.type, payload=message.model_dump(mode="json")
        )
    assert repository.list_pending_outbox(limit=1)[0].message_id == "ase_first"
    repository.mark_outbox_failed("ase_first", detail="Controller unavailable")
    assert repository.list_pending_outbox(limit=1)[0].message_id == "ase_second"
    repository.mark_outbox_sent("ase_second")
    assert repository.list_pending_outbox(limit=1)[0].message_id == "ase_first"


@pytest.mark.anyio
async def test_state_receipt_uses_the_installed_zenoh_query_protocol(
    tmp_path: Path,
) -> None:
    config = zenoh.Config()
    config.insert_json5("scouting/multicast/enabled", "false")
    config.insert_json5("listen/endpoints", "[]")
    message = observation()
    received = []

    def commit(query):
        received.append(json.loads(query.payload.to_string()))
        query.reply(
            str(query.key_expr),
            json.dumps(receipt()),
            encoding=zenoh.Encoding.APPLICATION_JSON,
        )

    session = zenoh.open(config)
    queryable = session.declare_queryable("iot/v1/agents/agt_test/state/commit", commit)
    client = transport(tmp_path, session)
    try:
        await client.publish_publications(
            enrollment=enrollment_record(), messages=(message,)
        )
        assert received == [message.model_dump(mode="json")]
    finally:
        queryable.undeclare()
        await client.close()
