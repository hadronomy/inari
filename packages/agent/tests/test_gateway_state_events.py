from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

import pytest
import rfc8785
from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from inari.db.migrations import DatabaseMigrator
from inari.documents import (
    DocumentKind,
    DocumentWork,
    ManagedAdmissionScope,
    ManagedSubmissionContext,
    RecordsReportSource,
    ReportBinding,
    ReportPrintOrigin,
    LabelDocument,
)
from inari.documents.fingerprint import (
    DeviceWorkFingerprintInput,
    fingerprint_device_work,
)
from inari.gateway.models import AgentManagedScope
from inari.gateway.repositories import GatewayRepository
from inari.gateway.state_events import GatewayStateEventProjector
from inari.runtime.store import RuntimeStore
from inari.security.secrets import MemorySecretStore
from inari.security.state_keys import AgentStateSigningKeyService
from inari.spool.filesystem import ArtifactFileStore
from tests import test_durable_spool_admission as spool_support


NOW = spool_support.NOW
SCOPE = AgentManagedScope(agent_id="agent-1", organization_id="org-1", site_id="site-1")
pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("ample_spool_volume")]


@pytest.fixture
async def admitted(tmp_path: Path):
    database_path = spool_support._migrate(tmp_path, purpose="label_document")
    runtime = RuntimeStore(database_path)
    repository = GatewayRepository(runtime)
    repository.record_inbound_command(
        command_id="command-1",
        message_id="dispatch-1",
        sequence=None,
        dispatch_epoch=1,
        message_type="controller.command.dispatch_device_work",
        payload={
            "payload": {
                "managed_work_id": "work-1",
                "authenticated_data": asdict(SCOPE),
            }
        },
    )
    context = ManagedSubmissionContext(
        contract_major=1,
        database="odoo",
        company_id="7",
        organization_id=SCOPE.organization_id,
        site_id=SCOPE.site_id,
        managed_work_id="work-1",
        print_intent_id="intent-1",
        origin_submission_key="report:origin-1",
        origin=ReportPrintOrigin(
            binding=ReportBinding(
                report_binding_id="report-binding-1",
                binding_revision_id="binding-1",
                report_action_id="stock.action_report_delivery",
                report_contract_digest="contract-digest",
                template_digest="template-digest",
                command_profile_id=None,
                layout_profile_id=None,
                hardware_matrix_digest="matrix-digest",
            ),
            route="automatic",
            source=RecordsReportSource(model="stock.picking", ordered_ids=(17,)),
            rendered_document_index=0,
            copy_ordinal=1,
        ),
        binding_revision_id="binding-1",
        device_id="device-1",
        actor_id="controller:primary",
        authorization_digest="dispatch-authorization",
        copy_ordinal=1,
    )
    work = DocumentWork(
        idempotency_key="work-1",
        context=context,
        document=LabelDocument(content=b"^XA^FDInari^FS^XZ"),
    )
    base = spool_support._admission(spool_support._jpeg_like_work())
    admission = replace(
        base,
        work=work,
        authorization_scope=ManagedAdmissionScope(
            managed_work_id=context.managed_work_id,
            organization_id=context.organization_id,
            site_id=context.site_id,
            database=context.database,
            actor_id=context.actor_id,
            device_id=context.device_id,
            binding_revision_id=context.binding_revision_id,
            operation=DocumentKind.LABEL_DOCUMENT,
            authorization_digest=context.authorization_digest,
        ),
        media_type="application/vnd.zebra-zpl",
        payload_fingerprint=fingerprint_device_work(
            DeviceWorkFingerprintInput(
                contract_major=1,
                operation=work.operation,
                device_id=context.device_id,
                media_type="application/vnd.zebra-zpl",
                document=work.document.content,
                options={},
                expires_at=base.deadline.expires_at,
            )
        ),
        authority_proof=replace(
            spool_support._authority_proof_for("device-1", purpose="label_document"),
            operation="label_document",
            media_type="application/vnd.zebra-zpl",
        ),
    )
    files = ArtifactFileStore(tmp_path / "spool")
    spool = spool_support._store(
        database_path,
        tmp_path / "spool",
        spool_support.FakeRootSecretStore(),
        spool_support.DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
        files=files,
    )
    await spool.accept(admission)
    return runtime, repository, spool, files, admission


def _projector(runtime, keys, *, boot="boot-1"):
    return GatewayStateEventProjector(
        store=runtime,
        signing_keys=keys,
        agent_boot_id=boot,
        clock=lambda: NOW + timedelta(seconds=10),
    )


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verified_claims(record, keys):
    compact = record.payload["event"]["payload"]["state_envelope"]
    protected, payload, signature = compact.split(".")
    public = keys.public_jwk()
    assert json.loads(_decode(protected)) == {
        "alg": "EdDSA",
        "kid": public["kid"],
        "typ": "application/inari-agent-state+jws",
    }
    Ed25519PublicKey.from_public_bytes(_decode(public["x"])).verify(
        _decode(signature), f"{protected}.{payload}".encode("ascii")
    )
    claims = json.loads(_decode(payload))
    assert _decode(payload) == rfc8785.dumps(claims)
    return claims


async def test_admission_without_acceptance_reply_is_signed_once_across_restart(
    admitted,
):
    runtime, repository, _, _, admission = admitted
    keys = AgentStateSigningKeyService(MemorySecretStore())
    inbound = repository.get_inbound_command("command-1")
    assert inbound.job_id is None
    assert inbound.state.value == "received"
    assert _projector(runtime, keys).project(scope=SCOPE, dispatch_epoch=1) == 1
    pending = repository.list_pending_outbox(recipient_scope=SCOPE)
    assert len(pending) == 1
    claims = _verified_claims(pending[0], keys)
    assert claims["agent_id"] == SCOPE.agent_id
    assert (
        claims["payload_fingerprint"] == "sha256:" + admission.payload_fingerprint.hex()
    )
    assert claims["job"]["print_job_id"] == "job-1"
    assert claims["job"]["managed_work_id"] == "work-1"
    assert claims["job"]["origin"]["record_ids"] == ["17"]
    assert claims["job"]["state"] == "accepted"
    assert (
        _projector(runtime, keys, boot="boot-2").project(scope=SCOPE, dispatch_epoch=1)
        == 0
    )
    assert repository.list_pending_outbox(recipient_scope=SCOPE) == pending


async def _lose_artifact(admitted):
    runtime, _, spool, files, _ = admitted
    with sqlite3.connect(runtime.database_path) as connection:
        storage_ref = connection.execute(
            "SELECT storage_ref FROM spool_artifacts WHERE job_id = 'job-1'"
        ).fetchone()[0]
    files.delete(storage_ref)
    assert (await spool.reconcile()).failed_print_jobs == 1


async def test_historical_states_survive_recovery_and_failed_batch_rolls_back(
    admitted, monkeypatch
):
    runtime, repository, _, _, _ = admitted
    await _lose_artifact(admitted)
    keys = AgentStateSigningKeyService(MemorySecretStore())
    sign = keys.sign
    calls = 0

    def fail_second(claims):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("signing unavailable")
        return sign(claims)

    with monkeypatch.context() as patch:
        patch.setattr(keys, "sign", fail_second)
        with pytest.raises(RuntimeError, match="signing unavailable"):
            _projector(runtime, keys).project(scope=SCOPE, dispatch_epoch=1)
    assert repository.list_pending_outbox(recipient_scope=SCOPE) == ()
    with sqlite3.connect(runtime.database_path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM gateway_print_job_cursors"
            ).fetchone()[0]
            == 0
        )
    assert _projector(runtime, keys).project(scope=SCOPE, dispatch_epoch=1) == 2
    claims = sorted(
        (
            _verified_claims(row, keys)
            for row in repository.list_pending_outbox(recipient_scope=SCOPE)
        ),
        key=lambda value: value["durable_state_sequence"],
    )
    assert [
        (value["job"]["state"], value["job"]["state_version"]) for value in claims
    ] == [("accepted", 1), ("failed", 2)]
    assert claims[0]["job"]["terminal_at"] is None
    assert claims[0]["job"]["error_code"] is None
    assert claims[1]["job"]["error_code"] == "service_unavailable"
    assert claims[0]["job"]["accepted_at"] == claims[1]["job"]["accepted_at"]


@pytest.mark.parametrize("field", ["agent_id", "organization_id", "site_id"])
async def test_projection_and_pending_events_are_isolated_by_enrollment(
    admitted, field
):
    runtime, repository, _, _, _ = admitted
    keys = AgentStateSigningKeyService(MemorySecretStore())
    other = replace(SCOPE, **{field: "other"})
    projector = _projector(runtime, keys)
    assert projector.project(scope=other, dispatch_epoch=1) == 0
    assert projector.project(scope=SCOPE, dispatch_epoch=1) == 1
    assert repository.list_pending_outbox(recipient_scope=other) == ()
    assert repository.list_pending_outbox() == ()
    assert len(repository.list_pending_outbox(recipient_scope=SCOPE, limit=1)) == 1


async def test_projection_preserves_bounded_progress_across_historical_events(admitted):
    runtime, repository, _, _, _ = admitted
    await _lose_artifact(admitted)
    keys = AgentStateSigningKeyService(MemorySecretStore())
    projector = _projector(runtime, keys)
    assert projector.project(scope=SCOPE, dispatch_epoch=1, limit=1) == 1
    assert projector.project(scope=SCOPE, dispatch_epoch=1, limit=1) == 1
    assert projector.project(scope=SCOPE, dispatch_epoch=1, limit=1) == 0
    assert len(repository.list_pending_outbox(recipient_scope=SCOPE)) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "another-job"),
        ("contract_version", "v99"),
        ("accepted_at", "2020-01-01T00:00:00Z"),
    ],
)
async def test_corrupt_event_identity_never_advances_projection(admitted, field, value):
    runtime, repository, _, _, _ = admitted
    with sqlite3.connect(runtime.database_path) as connection:
        connection.execute(
            "UPDATE public_print_job_events SET snapshot_json = json_set(snapshot_json, ?, ?)",
            (f"$.{field}", value),
        )
    keys = AgentStateSigningKeyService(MemorySecretStore())
    with pytest.raises(RuntimeError, match="Print Job event"):
        _projector(runtime, keys).project(scope=SCOPE, dispatch_epoch=1)
    assert repository.list_pending_outbox(recipient_scope=SCOPE) == ()
    with sqlite3.connect(runtime.database_path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM gateway_print_job_cursors"
            ).fetchone()[0]
            == 0
        )


async def test_migration_scopes_pending_managed_replies_before_delivery(tmp_path):
    path = tmp_path / "historical.sqlite3"
    migrator = DatabaseMigrator(path)
    command.upgrade(migrator._build_alembic_config(), "20260905_0014")
    timestamp = NOW.isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO gateway_inbound_commands (
            command_id, message_id, message_type, state, payload_json, received_at, updated_at
        ) VALUES ('command-1', 'dispatch-1', 'controller.command.dispatch_device_work', 'received', ?, ?, ?)""",
            (
                json.dumps({"payload": {"authenticated_data": asdict(SCOPE)}}),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO gateway_outbox (
            message_id, message_type, state, payload_json, correlation_id, created_at, updated_at
        ) VALUES ('reply-1', 'agent.command.accepted', 'pending', '{}', 'command-1', ?, ?)""",
            (timestamp, timestamp),
        )
    migrator.ensure_current()
    repository = GatewayRepository(RuntimeStore(path))
    assert repository.list_pending_outbox() == ()
    assert (
        repository.list_pending_outbox(recipient_scope=replace(SCOPE, agent_id="other"))
        == ()
    )
    assert [
        record.message_id
        for record in repository.list_pending_outbox(recipient_scope=SCOPE)
    ] == ["reply-1"]
