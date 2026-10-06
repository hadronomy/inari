from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
import sqlite3

import pytest
from alembic import command
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from PIL import Image
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner
from sqlalchemy import insert, select, update, func

from inari.client_trust import (
    ClientPairing,
    PairingRequest,
    Permission,
    RequestTarget,
    SqliteClientTrustStore,
)
from inari.core.failures import DomainFailure, ProblemCode
from inari.cli import app as cli_app
from inari.db import DatabaseMigrator
from inari.db.schema import (
    devices_table,
    device_tests_table,
    device_test_evidence_table,
    client_grants_table,
    device_authority_revocations_table,
    device_authority_signer_keys_table,
)
from inari.device_authority import (
    AuthorityError,
    DeviceCapabilityAuthority,
    OutputEvidence as AuthorityOutputEvidence,
    RevocationSubjectKind,
    SignerPurpose,
    SignerRecord,
    SignerState,
    SqliteDeviceAuthorityReader,
    canonical_digest,
    canonical_json_bytes,
)
from inari.device_authority.bundle import AuthorityBundle
from inari.device_authority.install import DeviceAuthorityInstaller
from inari.device_tests import DeviceTestRequest, DeviceTestService, TestState
from inari.printing.receipt_pattern import (
    PATTERN_DIGEST,
    REQUIRED_CHECKS,
    receipt_image,
)
from inari.device_tests.models import TestWorkerClaim
from inari.device_tests.signing import DeviceTestSigningKey
from inari.device_tests.sqlite import SqliteDeviceTestLedger
from inari.local_api.app import create_app
from inari.local_api.header_authorization import (
    AuthorizationDecision,
    AuthorizationFailure,
    AuthorizationMode,
)
from inari.physical_execution._ledger import SqliteExecutionLedger
from inari.physical_execution.models import DriverExecutionResult, DriverOutcome
from inari.printing.renderers.image_escpos_renderer import EscPosImageReceiptRenderer
from inari.print_jobs import OutputEvidence
from inari.runtime.models import DeviceRecord
from inari.runtime.devices.service import DeviceCatalog
from inari.runtime.store import RuntimeStore
from inari.security.secrets import MemorySecretStore
from inari.spool import SqlActiveAuthorityGuard

from .test_device_capability_authority import NOW, _fixture, _refresh_manifest, _signed
from .test_drawer_intents import Clock, Drawer, authorization as drawer_authorization
from .test_api import make_test_container


@dataclass
class Catalog(DeviceCatalog):
    device: DeviceRecord

    async def refresh(self):
        return (self.device,)

    def get_device(self, device_id):
        return self.device if device_id == self.device.id else None


class Worker:
    def __init__(self, ledger, authorization):
        self.ledger = ledger
        self.authorization = authorization
        self.calls = 0
        self.preparations = 0
        self.claim = None
        self.ready_error = None
        self.execute_error = None
        self.result = DriverExecutionResult(
            DriverOutcome.CONFIRMED, OutputEvidence.SPOOLER, "123"
        )
        self.entered = asyncio.Event()
        self.release = None
        self.closed = False
        self.on_ready = None
        self.on_close = None

    async def prepare(self, work):
        self.preparations += 1
        with self.ledger.store.connection() as connection:
            row = (
                connection.execute(
                    select(device_tests_table).where(
                        device_tests_table.c.device_id == work.device_id,
                        device_tests_table.c.state == TestState.ACCEPTED.value,
                    )
                )
                .mappings()
                .one()
            )
        assert row["worker_claim_id"] is not None
        self.claim = TestWorkerClaim(
            row["record_id"], work.device_id, row["worker_claim_id"]
        )
        self.work = work
        return self

    async def wait_ready(self):
        if self.on_ready:
            self.on_ready()
        if self.ready_error:
            raise self.ready_error

    async def execute(self, marker):
        with self.ledger.store.connection() as connection:
            row = (
                connection.execute(
                    select(device_tests_table).where(
                        device_tests_table.c.record_id == marker.execution_id
                    )
                )
                .mappings()
                .one()
            )
        assert row["state"] == "in_progress"
        assert row["marker_id"] == marker.marker_id
        assert row["started_at"] is not None
        self.calls += 1
        self.entered.set()
        if self.release:
            await self.release.wait()
        if self.execute_error:
            raise self.execute_error
        return self.result

    async def close(self):
        if self.on_close:
            self.on_close()
        self.closed = True


def _authorization(scope):
    source = drawer_authorization()
    business = replace(
        source.grant.scope.business,
        database=scope.database,
        organization_id=scope.organization_id,
        site_id=scope.site_id,
        pos_configuration_id=scope.pos_configuration_id,
    )
    grant_scope = replace(source.grant.scope, business=business)
    target = RequestTarget("POST", "https://agent.example/v1/device-tests")
    grant = replace(
        source.grant,
        scope=grant_scope,
        role="device_manager",
        permissions=frozenset({Permission.DEVICE_TEST}),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=15),
    )
    return replace(
        source,
        target=target,
        grant=grant,
        dpop=replace(source.dpop, htu=target.uri, iat=NOW, accepted_at=NOW),
        endpoint=replace(
            source.endpoint,
            business=business,
            allowed_methods=frozenset({"POST", "GET"}),
            allowed_paths=("/v1/device-tests", "/v1/device-tests/*"),
        ),
        accepted_at=NOW,
    )


def _save_grant(store, authorization):
    grant = authorization.grant
    trust = SqliteClientTrustStore(store.database_path)
    trust.save_pairing_request(
        PairingRequest(
            request_id=f"request-{grant.pairing_id}",
            scope=grant.scope,
            browser_jwk_thumbprint=grant.jwk_thumbprint,
            requested_permissions=grant.permissions,
            session_nonce="session-nonce",
            phrase="alpha-bravo-charlie",
            created_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=15),
        )
    )
    trust.save_pairing(
        ClientPairing(
            pairing_id=grant.pairing_id,
            pairing_request_id=f"request-{grant.pairing_id}",
            jwk_thumbprint=grant.jwk_thumbprint,
            scope=grant.scope,
            actor_id=grant.actor_id,
            role=grant.role,
            permissions=grant.permissions,
            created_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(days=1),
        )
    )
    trust.save_grant(grant)


@pytest.fixture
def rig(tmp_path):
    projections, observations, target, _ = _fixture()
    options = canonical_digest({})
    profile = replace(
        projections.profile.profile,
        capabilities=(
            replace(
                projections.profile.profile.capabilities[0],
                options_digest=options,
                output_evidence=AuthorityOutputEvidence.SPOOLER,
            ),
        ),
    )

    def signed_replace(signed, field, payload):
        digest, _, signature = _signed(
            payload,
            projections.private_keys[signed.signer_key_id],
            signed.signer_key_id,
        )
        return replace(signed, **{field: payload}, digest=digest, signature=signature)

    projections.profile = signed_replace(projections.profile, "profile", profile)
    projections.row = signed_replace(
        projections.row,
        "row",
        replace(projections.row.row, driver_profile_digest=projections.profile.digest),
    )
    projections.binding = signed_replace(
        projections.binding,
        "revision",
        replace(
            projections.binding.revision,
            driver_profile_digest=projections.profile.digest,
            options_digest=options,
        ),
    )
    observation = replace(
        observations.current.observation,
        driver_profile_digest=projections.profile.digest,
    )
    digest, _, signature = _signed(
        observation, observations.private_key, observations.current.signer_key_id
    )
    observations.current = replace(
        observations.current,
        observation=observation,
        digest=digest,
        signature=signature,
    )
    secrets = MemorySecretStore()
    key = DeviceTestSigningKey(secrets)
    projections.signers[key.key_id()] = SignerRecord(
        key.key_id(),
        SignerPurpose.DEVICE_TEST_EVIDENCE,
        key.public_key(),
        SignerState.ACTIVE,
        NOW - timedelta(days=1),
        NOW + timedelta(days=90),
        None,
    )
    _refresh_manifest(projections)
    assert projections.manifest is not None
    manifest = projections.manifest.model_copy(
        update={"evidence": (), "activations": ()}
    )
    revision = replace(
        projections.authority_state.current_revision.revision,
        manifest_digest=canonical_digest(manifest.model_dump(mode="json")),
    )
    bundle = AuthorityBundle(
        revision=signed_replace(
            projections.authority_state.current_revision, "revision", revision
        ),
        manifest=manifest,
    )
    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()
    store = RuntimeStore(path)
    with store.connection() as connection:
        connection.execute(
            insert(devices_table).values(
                id=target.device_id,
                kind="printer",
                driver_key=profile.driver_id,
                identity_transport="spooler",
                name="Fixture Device",
                connection_state="online",
                first_seen_at=NOW.isoformat(),
                last_seen_at=NOW.isoformat(),
                updated_at=NOW.isoformat(),
                is_default=False,
                capabilities_json="{}",
                metadata_json="{}",
            )
        )
    DeviceAuthorityInstaller(
        store,
        trusted_signer=projections.signers[bundle.revision.signer_key_id],
        agent_id=manifest.agent_id,
        scope=target.scope,
    ).install(bundle, now=NOW)
    reader = SqliteDeviceAuthorityReader(store)
    authority = DeviceCapabilityAuthority(
        projections=reader, observations=observations, current_agent_version="1.20.0"
    )
    authorization = _authorization(target.scope)
    _save_grant(store, authorization)
    ledger = SqliteDeviceTestLedger(store)
    device = DeviceRecord.from_printer(Drawer().open_cash_drawer("device-1").printer)
    catalog = Catalog(
        replace(device, id=target.device_id, driver_key=profile.driver_id)
    )
    worker = Worker(ledger, authorization)
    clock = Clock(NOW)
    service = DeviceTestService(
        ledger,
        authority,
        reader,
        catalog,
        EscPosImageReceiptRenderer(),
        worker,
        key,
        clock,
    )
    request = DeviceTestRequest("test-1", target.device_id, target.binding_revision_id)
    yield service, authorization, request, worker, clock, bundle, secrets
    store.engine.dispose()


def _checks(answer="correct"):
    return dict.fromkeys(REQUIRED_CHECKS, answer)


def _count(rig, table):
    with rig[0].ledger.store.connection() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar_one()


@pytest.mark.anyio
async def test_spooler_result_waits_for_checks_then_signs_exact_graph(rig):
    service, authorization, request, worker, _, bundle, *_ = rig
    accepted = await service.submit(request, authorization)
    await service.execute(accepted)
    record = service.get(request.test_id, authorization)
    assert record.state is TestState.AWAITING_CHECKS
    assert record.signed_result is None
    assert _count(rig, device_test_evidence_table) == 0
    assert worker.closed and worker.calls == 1
    assert worker.work.content.startswith(b"\x1b@\x1dv0")
    result = service.finalize(request.test_id, _checks(), authorization)
    assert result.state is TestState.COMPLETED
    signed = result.signed_result
    assert signed["result"]["outcome"] == "passed"
    assert signed["result"]["checks"] == _checks()
    assert signed["result"]["actor_id"] == authorization.grant.actor_id
    assert (
        signed["result"]["graph"]["binding_digest"]
        == bundle.manifest.bindings[0].digest
    )
    Ed25519PublicKey.from_public_bytes(service.signing_key.public_key()).verify(
        bytes.fromhex(signed["signature"]), canonical_json_bytes(signed["result"])
    )
    changed = {**signed["result"], "actor_id": "another-actor"}
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(service.signing_key.public_key()).verify(
            bytes.fromhex(signed["signature"]), canonical_json_bytes(changed)
        )
    assert _count(rig, device_test_evidence_table) == 1
    with pytest.raises(AuthorityError):
        service.authority.authorize(accepted.permit.authorization.target, now=NOW)


@pytest.mark.anyio
async def test_retries_and_concurrent_execution_never_print_another_receipt(rig):
    service, authorization, request, worker, *_ = rig
    accepted, retry = await asyncio.gather(
        service.submit(request, authorization), service.submit(request, authorization)
    )
    assert accepted.replayed != retry.replayed
    await asyncio.gather(service.execute(accepted), service.execute(retry))
    completed = service.finalize(request.test_id, _checks(), authorization)
    assert service.finalize(request.test_id, _checks(), authorization) == completed
    replay = await service.submit(request, authorization)
    assert replay.replayed and replay.permit is None
    assert worker.calls == 1 and _count(rig, device_tests_table) == 1
    with pytest.raises(DomainFailure) as error:
        await service.submit(
            replace(request, device_id="another-device"), authorization
        )
    assert error.value.code is ProblemCode.IDEMPOTENCY_CONFLICT
    with pytest.raises(DomainFailure):
        service.finalize(request.test_id, _checks("incorrect"), authorization)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("answer", "outcome"),
    [("incorrect", "failed_contract"), ("not_run", "failed_environment")],
)
async def test_physical_answers_define_the_test_outcome(rig, answer, outcome):
    service, authorization, request, *_ = rig
    accepted = await service.submit(request, authorization)
    await service.execute(accepted)
    answers = _checks()
    answers["qr"] = answer
    result = service.finalize(request.test_id, answers, authorization)
    assert result.signed_result["result"]["outcome"] == outcome


@pytest.mark.anyio
async def test_uncertain_io_is_not_repeated_or_signed_as_passed(rig):
    service, authorization, request, worker, *_ = rig
    worker.execute_error = RuntimeError("Lost platform result")
    await service.execute(await service.submit(request, authorization))
    assert (
        service.get(request.test_id, authorization).state is TestState.OUTCOME_UNKNOWN
    )
    assert (await service.submit(request, authorization)).permit is None
    result = service.finalize(request.test_id, _checks(), authorization)
    assert result.signed_result["result"]["outcome"] == "failed_environment"
    assert result.signed_result["result"]["evidence"] is None
    assert worker.calls == 1


@pytest.mark.anyio
async def test_missing_physical_answers_and_early_answers_are_rejected(rig):
    service, authorization, request, *_ = rig
    await service.submit(request, authorization)
    with pytest.raises(DomainFailure):
        service.finalize(request.test_id, {"text": "correct"}, authorization)
    with pytest.raises(DomainFailure):
        service.finalize(request.test_id, _checks(), authorization)
    assert _count(rig, device_test_evidence_table) == 0


@pytest.mark.anyio
async def test_revoked_grant_blocks_replay_lookup_and_physical_io(rig):
    service, authorization, request, worker, *_ = rig
    accepted = await service.submit(request, authorization)
    with service.ledger.store.immediate_transaction() as connection:
        connection.execute(update(client_grants_table).values(lifecycle="revoked"))
    for operation in (
        lambda: service.get(request.test_id, authorization),
        lambda: service.finalize(request.test_id, _checks(), authorization),
    ):
        with pytest.raises(DomainFailure):
            operation()
    with pytest.raises(DomainFailure):
        await service.submit(request, authorization)
    await service.execute(accepted)
    assert worker.calls == 0
    assert worker.preparations == 0


@pytest.mark.anyio
async def test_revoked_graph_cannot_sign_a_passed_result(rig):
    service, authorization, request, _, _, bundle, _ = rig
    await service.execute(await service.submit(request, authorization))
    key = Ed25519PrivateKey.generate()
    with service.ledger.store.immediate_transaction() as connection:
        connection.execute(
            insert(device_authority_signer_keys_table).values(
                key_id="revocation-key",
                purpose="authority_revocation",
                public_key=key.public_key().public_bytes_raw(),
                state="active",
                not_before=(NOW - timedelta(days=1)).isoformat(),
            )
        )
        connection.execute(
            insert(device_authority_revocations_table).values(
                revocation_id="revocation-1",
                subject_kind=RevocationSubjectKind.BINDING_REVISION.value,
                subject_id=request.binding_revision_id,
                subject_digest=bytes.fromhex(bundle.manifest.bindings[0].digest),
                reason_code="withdrawn",
                revoked_at=NOW.isoformat(),
                authority_revision_id=bundle.revision.revision.revision_id,
                signer_key_id="revocation-key",
                signature=key.sign(
                    canonical_json_bytes(
                        {
                            "binding_digest": bundle.manifest.bindings[0].digest,
                            "reason_code": "withdrawn",
                        }
                    )
                ),
            )
        )
    with pytest.raises(DomainFailure) as error:
        service.finalize(request.test_id, _checks(), authorization)
    assert error.value.code is ProblemCode.CAPABILITY_CHANGED
    assert _count(rig, device_test_evidence_table) == 0


@pytest.mark.anyio
async def test_deadline_poll_does_not_release_a_live_worker(rig):
    service, authorization, request, worker, clock, *_ = rig
    worker.release = asyncio.Event()
    accepted = await service.submit(request, authorization)
    execution = asyncio.create_task(service.execute(accepted))
    await worker.entered.wait()
    clock.value = NOW + timedelta(seconds=31)
    assert service.get(request.test_id, authorization).state is TestState.IN_PROGRESS
    business = SqliteExecutionLedger(
        store=service.ledger.store, authority_guard=SqlActiveAuthorityGuard()
    )
    with service.ledger.store.connection() as connection:
        assert business._device_is_busy(
            connection, device_id=request.device_id, now=clock.value
        )
    worker.release.set()
    await execution
    assert worker.closed
    assert (
        service.get(request.test_id, authorization).state is TestState.AWAITING_CHECKS
    )


@pytest.mark.anyio
async def test_cancellation_closes_worker_before_releasing_reservation(rig):
    service, authorization, request, worker, *_ = rig
    worker.release = asyncio.Event()
    accepted = await service.submit(request, authorization)
    execution = asyncio.create_task(service.execute(accepted))
    await worker.entered.wait()
    worker.on_close = lambda: (
        pytest.fail("Reservation released before close")
        if service.get(request.test_id, authorization).state
        is not TestState.IN_PROGRESS
        else None
    )
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert worker.closed
    assert (
        service.get(request.test_id, authorization).state is TestState.OUTCOME_UNKNOWN
    )


@pytest.mark.anyio
async def test_startup_recovery_never_repeats_abandoned_io(rig):
    service, authorization, request, worker, clock, *_ = rig
    accepted = await service.submit(request, authorization)
    claim = service.ledger.claim_worker(accepted.record, authorization, now=NOW)
    assert claim is not None
    service.ledger.mark_io_started(
        claim,
        authorization,
        lambda: service.authority.check_test(accepted.permit, now=NOW),
        now=NOW,
    )
    service.ledger.recover_after_restart(now=NOW + timedelta(seconds=1))
    clock.value = NOW + timedelta(seconds=1)
    assert (
        service.get(request.test_id, authorization).state is TestState.OUTCOME_UNKNOWN
    )
    assert (await service.submit(request, authorization)).permit is None
    assert worker.calls == 0


def test_pattern_and_protected_key_remain_stable(rig):
    service = rig[0]
    secrets = rig[6]
    image = receipt_image()
    assert sha256(image).hexdigest() == PATTERN_DIGEST
    assert Image.open(BytesIO(image)).size == (576, 1056)
    assert (
        DeviceTestSigningKey(secrets).public_key() == service.signing_key.public_key()
    )


def test_upgrade_from_previous_schema_preserves_installed_authority(rig):
    service = rig[0]
    before = service.projections.read_authority_state()
    path = service.ledger.store.database_path
    migrator = DatabaseMigrator(path)
    command.downgrade(migrator._build_alembic_config(), "20261005_0017")
    result = migrator.ensure_current()
    assert result.previous_revision == "20261005_0017"
    assert result.current_revision == "20261006_0018"
    assert result.backup_path is not None
    assert service.projections.read_authority_state() == before
    assert _count(rig, device_tests_table) == 0
    with sqlite3.connect(result.backup_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("20261005_0017",)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'device_tests'"
            ).fetchone()
            is None
        )


def test_public_evidence_key_command_uses_protected_storage(rig, monkeypatch):
    service, *_, secrets = rig
    monkeypatch.setattr(
        "inari.commands.authority.build_secret_store", lambda settings: secrets
    )
    result = CliRunner().invoke(cli_app, ["authority", "test-key"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "key_id": service.signing_key.key_id(),
        "public_key": service.signing_key.public_key().hex(),
    }


@dataclass
class TestAuthorizer:
    __test__ = False
    authorization: object
    denied: bool = False

    def authorize(self, request, policy):
        assert request.path.startswith("/v1/device-tests")
        assert policy.mode is AuthorizationMode.CLIENT_GRANT
        assert policy.permission is Permission.DEVICE_TEST
        if self.denied:
            raise AuthorizationFailure(
                "permission_denied", "Device Test permission is required.", status=403
            )
        return AuthorizationDecision.authorized(self.authorization)


def _test_app(rig, mocker, *, denied=False):
    service, authorization, *_ = rig
    container = replace(
        make_test_container(mocker=mocker),
        device_test_service=service,
        device_work_authorizer=TestAuthorizer(authorization, denied),
    )
    return create_app(container=container)


def _test_body(request):
    return {
        "contract_major": 1,
        "device_test_id": request.test_id,
        "device_id": request.device_id,
        "binding_revision_id": request.binding_revision_id,
    }


@pytest.mark.anyio
async def test_http_test_flow_preserves_one_execution_and_immutable_answers(
    rig, mocker
):
    _, _, request, worker, *_ = rig
    app = _test_app(rig, mocker)
    async with AsyncClient(
        transport=ASGITransport(app), base_url="https://agent.example"
    ) as client:
        accepted = await client.post(
            "/v1/device-tests",
            json=_test_body(request),
            headers={"Idempotency-Key": request.test_id},
        )
        assert accepted.status_code == 202
        assert accepted.json()["state"] == "accepted"
        assert accepted.json()["signed_result"] is None
        observed = await client.get(f"/v1/device-tests/{request.test_id}")
        assert observed.status_code == 200
        assert observed.json()["state"] == "awaiting_checks"
        completed = await client.post(
            f"/v1/device-tests/{request.test_id}/checks", json=_checks()
        )
        assert completed.status_code == 200
        assert completed.json()["signed_result"]["result"]["outcome"] == "passed"
        replay = await client.post(
            "/v1/device-tests",
            json=_test_body(request),
            headers={"Idempotency-Key": request.test_id},
        )
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["signed_result"] == completed.json()["signed_result"]
        repeated_checks = await client.post(
            f"/v1/device-tests/{request.test_id}/checks", json=_checks()
        )
        assert repeated_checks.json() == completed.json()
        changed = await client.post(
            f"/v1/device-tests/{request.test_id}/checks", json=_checks("incorrect")
        )
        assert changed.status_code == 409
    assert worker.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        {"content": "business receipt"},
        {"printer_name": "POS-80"},
        {"contract_major": 2},
    ],
)
async def test_http_device_test_rejects_business_content_and_unsupported_contracts(
    rig, mocker, change
):
    _, _, request, worker, *_ = rig
    async with AsyncClient(
        transport=ASGITransport(_test_app(rig, mocker)),
        base_url="https://agent.example",
    ) as client:
        response = await client.post(
            "/v1/device-tests",
            json={**_test_body(request), **change},
            headers={"Idempotency-Key": request.test_id},
        )
    assert response.status_code == 422
    assert worker.calls == 0 and _count(rig, device_tests_table) == 0


@pytest.mark.anyio
async def test_http_device_test_id_must_match_idempotency_key(rig, mocker):
    _, _, request, worker, *_ = rig
    async with AsyncClient(
        transport=ASGITransport(_test_app(rig, mocker)),
        base_url="https://agent.example",
    ) as client:
        response = await client.post(
            "/v1/device-tests",
            json=_test_body(request),
            headers={"Idempotency-Key": "another-test"},
        )
    assert response.status_code == 422
    assert worker.calls == 0 and _count(rig, device_tests_table) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("test_id", ["batch/123", "batch%2F123", "batch\\123"])
async def test_http_device_test_rejects_unaddressable_ids(rig, mocker, test_id):
    _, _, request, worker, *_ = rig
    async with AsyncClient(
        transport=ASGITransport(_test_app(rig, mocker)),
        base_url="https://agent.example",
    ) as client:
        response = await client.post(
            "/v1/device-tests",
            json={**_test_body(request), "device_test_id": test_id},
            headers={"Idempotency-Key": test_id},
        )
    assert response.status_code == 422
    assert worker.preparations == 0
    assert worker.calls == 0 and _count(rig, device_tests_table) == 0


@pytest.mark.parametrize("test_id", ["batch/123", "batch%2F123", "batch\\123"])
def test_device_test_request_rejects_unaddressable_ids(test_id):
    with pytest.raises(ValueError, match="test_id"):
        DeviceTestRequest(test_id, "device-1", "revision-1")


@pytest.mark.anyio
async def test_http_device_test_addresses_permitted_identifier_characters(rig, mocker):
    _, _, request, worker, *_ = rig
    request = replace(request, test_id="batch:123.a_b-4")
    async with AsyncClient(
        transport=ASGITransport(_test_app(rig, mocker)),
        base_url="https://agent.example",
    ) as client:
        accepted = await client.post(
            "/v1/device-tests",
            json=_test_body(request),
            headers={"Idempotency-Key": request.test_id},
        )
        assert accepted.status_code == 202
        observed = await client.get(f"/v1/device-tests/{request.test_id}")
        assert observed.status_code == 200
        completed = await client.post(
            f"/v1/device-tests/{request.test_id}/checks", json=_checks()
        )
        assert completed.status_code == 200
        assert completed.json()["signed_result"]["result"]["outcome"] == "passed"
    assert worker.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_http_device_test_requires_one_idempotency_header(rig, mocker, duplicate):
    _, _, request, worker, *_ = rig
    headers = (
        [("Idempotency-Key", request.test_id), ("Idempotency-Key", request.test_id)]
        if duplicate
        else []
    )
    async with AsyncClient(
        transport=ASGITransport(_test_app(rig, mocker)),
        base_url="https://agent.example",
    ) as client:
        response = await client.post(
            "/v1/device-tests", json=_test_body(request), headers=headers
        )
    assert response.status_code == (400 if duplicate else 422)
    assert response.headers["content-type"] == "application/problem+json"
    assert worker.preparations == 0
    assert worker.calls == 0 and _count(rig, device_tests_table) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/v1/device-tests", "/v1/device-tests/test-1/checks"])
async def test_http_device_test_denies_permission_before_reading_body(
    rig, mocker, path
):
    body_read = False

    async def body():
        nonlocal body_read
        body_read = True
        yield b"invalid JSON"

    async with AsyncClient(
        transport=ASGITransport(_test_app(rig, mocker, denied=True)),
        base_url="https://agent.example",
    ) as client:
        response = await client.post(
            path, content=body(), headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert not body_read


@pytest.mark.anyio
async def test_expired_unstarted_test_releases_business_fence_without_io(rig):
    service, authorization, request, worker, clock, *_ = rig
    accepted = await service.submit(request, authorization)
    business = SqliteExecutionLedger(
        store=service.ledger.store, authority_guard=SqlActiveAuthorityGuard()
    )
    with service.ledger.store.connection() as connection:
        assert business._device_is_busy(
            connection, device_id=request.device_id, now=NOW
        )
        assert not business._device_is_busy(
            connection, device_id=request.device_id, now=NOW + timedelta(seconds=31)
        )
    clock.value = NOW + timedelta(seconds=31)
    await service.execute(accepted)
    assert (
        service.get(request.test_id, authorization).state
        is TestState.FAILED_ENVIRONMENT
    )
    assert worker.calls == 0


@pytest.mark.anyio
async def test_platform_evidence_below_the_contract_cannot_pass(rig):
    service, authorization, request, worker, *_ = rig
    worker.result = DriverExecutionResult(
        DriverOutcome.CONFIRMED, OutputEvidence.TRANSPORT, "123"
    )
    await service.execute(await service.submit(request, authorization))
    result = service.finalize(request.test_id, _checks(), authorization)
    assert result.signed_result["result"]["outcome"] == "failed_environment"


@pytest.mark.anyio
async def test_removed_evidence_signer_blocks_finalization(rig):
    service, authorization, request, *_ = rig
    await service.execute(await service.submit(request, authorization))
    with service.ledger.store.immediate_transaction() as connection:
        connection.execute(
            update(device_authority_signer_keys_table)
            .where(
                device_authority_signer_keys_table.c.key_id
                == service.signing_key.key_id()
            )
            .values(state="retired", retired_at=NOW.isoformat())
        )
    with pytest.raises(DomainFailure) as error:
        service.finalize(request.test_id, _checks(), authorization)
    assert error.value.code is ProblemCode.CERTIFICATION_REQUIRED
    assert _count(rig, device_test_evidence_table) == 0


@pytest.mark.anyio
async def test_another_manager_pairing_cannot_read_replay_or_finalize_the_test(rig):
    service, authorization, request, worker, *_ = rig
    await service.execute(await service.submit(request, authorization))
    other = replace(
        authorization,
        grant=replace(
            authorization.grant,
            grant_id="grant-other",
            pairing_id="pairing-other",
            actor_id="res.users:8",
        ),
    )
    _save_grant(service.ledger.store, other)
    for action in (
        lambda: service.get(request.test_id, other),
        lambda: service.finalize(request.test_id, _checks(), other),
    ):
        with pytest.raises(DomainFailure) as error:
            action()
        assert error.value.code is ProblemCode.RESOURCE_NOT_FOUND
    with pytest.raises(DomainFailure) as error:
        await service.submit(request, other)
    assert error.value.code is ProblemCode.PERMISSION_DENIED
    assert service.get(request.test_id, authorization).signed_result is None
    assert worker.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["before_io", "unknown", "weak_evidence"])
@pytest.mark.parametrize("answer", ["correct", "incorrect"])
async def test_missing_execution_evidence_cannot_prove_a_contract_result(
    rig, failure, answer
):
    service, authorization, request, worker, *_ = rig
    if failure == "before_io":
        worker.ready_error = RuntimeError("Device unavailable")
    elif failure == "unknown":
        worker.execute_error = RuntimeError("Lost result")
    else:
        worker.result = DriverExecutionResult(
            DriverOutcome.CONFIRMED, OutputEvidence.TRANSPORT, "123"
        )
    await service.execute(await service.submit(request, authorization))
    record = service.finalize(request.test_id, _checks(answer), authorization)
    assert record.signed_result["result"]["outcome"] == "failed_environment"
    assert worker.calls == (0 if failure == "before_io" else 1)


@pytest.mark.anyio
async def test_expired_test_never_starts_worker_preparation(rig):
    service, authorization, request, worker, clock, *_ = rig
    accepted = await service.submit(request, authorization)
    clock.value = NOW + timedelta(seconds=31)
    await service.execute(accepted)
    assert worker.preparations == 0
    assert worker.calls == 0
    assert worker.closed is False
    record = service.get(request.test_id, authorization)
    assert record.state is TestState.FAILED_ENVIRONMENT
    assert record.error_code == "execution_deadline"


@pytest.mark.anyio
async def test_delayed_execution_cannot_start_another_worker(rig):
    service, authorization, request, worker, *_ = rig
    worker.release = asyncio.Event()
    accepted = await service.submit(request, authorization)
    execution = asyncio.create_task(service.execute(accepted))
    await worker.entered.wait()
    await service.execute(accepted)
    assert worker.preparations == 1
    assert worker.calls == 1
    assert worker.closed is False
    worker.release.set()
    await execution
    assert worker.closed is True
    assert (
        service.get(request.test_id, authorization).state is TestState.AWAITING_CHECKS
    )


@pytest.mark.anyio
async def test_another_worker_claim_cannot_change_execution(rig):
    service, authorization, request, worker, *_ = rig
    accepted = await service.submit(request, authorization)
    claim = service.ledger.claim_worker(accepted.record, authorization, now=NOW)
    assert claim is not None
    assert service.ledger.claim_worker(accepted.record, authorization, now=NOW) is None
    other = replace(
        claim,
        claim_id=("0" if claim.claim_id[0] != "0" else "1") + claim.claim_id[1:],
    )
    before = service.get(request.test_id, authorization)
    assert (
        service.ledger.mark_io_started(
            other,
            authorization,
            lambda: pytest.fail("Another worker claim cannot authorize I/O."),
            now=NOW,
        )
        is None
    )
    service.ledger.mark_worker_stop_failed(other)
    service.ledger.fail_before_io(other, now=NOW)
    assert service.get(request.test_id, authorization) == before
    marker = service.ledger.mark_io_started(
        claim,
        authorization,
        lambda: service.authority.check_test(accepted.permit, now=NOW),
        now=NOW,
    )
    assert marker is not None
    started = service.get(request.test_id, authorization)
    service.ledger.finish_io(other, worker.result, now=NOW)
    assert service.get(request.test_id, authorization) == started


@pytest.mark.anyio
@pytest.mark.parametrize("stop_fails", [False, True])
async def test_preparing_worker_keeps_device_reserved_after_deadline(rig, stop_fails):
    service, authorization, request, worker, clock, *_ = rig
    business = SqliteExecutionLedger(
        store=service.ledger.store, authority_guard=SqlActiveAuthorityGuard()
    )
    observed = []

    def expire_during_preparation():
        clock.value = NOW + timedelta(seconds=31)
        record = service.get(request.test_id, authorization)
        with service.ledger.store.connection() as connection:
            busy = business._device_is_busy(
                connection, device_id=request.device_id, now=clock.value
            )
        observed.append((record, busy))

    def fail_to_stop():
        raise RuntimeError("Worker still alive")

    worker.on_ready = expire_during_preparation
    worker.on_close = fail_to_stop if stop_fails else None
    accepted = await service.submit(request, authorization)
    if stop_fails:
        with pytest.raises(RuntimeError, match="still alive"):
            await service.execute(accepted)
    else:
        await service.execute(accepted)
    record_during_preparation, busy = observed[0]
    assert record_during_preparation.state is TestState.ACCEPTED
    assert busy is True
    assert worker.calls == 0
    record = service.get(request.test_id, authorization)
    assert record.state is (
        TestState.ACCEPTED if stop_fails else TestState.FAILED_ENVIRONMENT
    )
    assert record.error_code == (
        "worker_stop_failed" if stop_fails else "execution_deadline"
    )
    with service.ledger.store.connection() as connection:
        assert (
            business._device_is_busy(
                connection, device_id=request.device_id, now=clock.value
            )
            is stop_fails
        )


@pytest.mark.anyio
@pytest.mark.parametrize("before_io", [False, True])
async def test_worker_stop_failure_remains_visible_and_fences_the_device(
    rig, before_io
):
    service, authorization, request, worker, clock, *_ = rig
    if before_io:
        worker.ready_error = RuntimeError("Device unavailable")

    def fail_to_stop():
        raise RuntimeError("Worker still alive")

    worker.on_close = fail_to_stop
    accepted = await service.submit(request, authorization)
    with pytest.raises(RuntimeError, match="still alive"):
        await service.execute(accepted)
    clock.value = NOW + timedelta(seconds=31)
    record = service.get(request.test_id, authorization)
    assert record.state is (TestState.ACCEPTED if before_io else TestState.IN_PROGRESS)
    assert record.error_code == "worker_stop_failed"
    assert record.signed_result is None
    assert worker.claim is not None
    service.ledger.fail_before_io(worker.claim, now=clock.value)
    assert service.get(request.test_id, authorization) == record
    service.ledger.finish_io(worker.claim, worker.result, now=clock.value)
    assert service.get(request.test_id, authorization) == record
    assert (
        service.ledger.mark_io_started(
            worker.claim,
            authorization,
            lambda: pytest.fail("An unproved worker stop cannot authorize Device I/O."),
            now=clock.value,
        )
        is None
    )
    business = SqliteExecutionLedger(
        store=service.ledger.store, authority_guard=SqlActiveAuthorityGuard()
    )
    with service.ledger.store.connection() as connection:
        assert business._device_is_busy(
            connection, device_id=request.device_id, now=clock.value
        )
    with pytest.raises(DomainFailure):
        service.finalize(request.test_id, _checks(), authorization)
    with pytest.raises(DomainFailure) as error:
        await service.submit(replace(request, test_id="test-2"), authorization)
    assert error.value.code is ProblemCode.CAPABILITY_CHANGED
    with pytest.raises(DomainFailure) as error:
        service.ledger.admit(
            replace(request, test_id="test-2"),
            authorization,
            record.fingerprint,
            now=clock.value,
            deadline=clock.value + timedelta(seconds=30),
        )
    assert error.value.code is ProblemCode.DEVICE_UNAVAILABLE
    assert (await service.submit(request, authorization)).permit is None
    assert worker.calls == (0 if before_io else 1)

    worker.on_close = None
    await worker.close()
    service.ledger.recover_after_restart(now=clock.value)
    recovered = service.get(request.test_id, authorization)
    assert recovered.state is (
        TestState.FAILED_ENVIRONMENT if before_io else TestState.OUTCOME_UNKNOWN
    )
    assert recovered.error_code == "worker_stop_failed"
    assert (await service.submit(request, authorization)).permit is None
    assert worker.calls == (0 if before_io else 1)
