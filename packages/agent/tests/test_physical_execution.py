from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import asyncio
import sqlite3

from PIL import Image
from alembic import command
import pytest

from inari.client_trust import (
    BoundOrigin,
    BusinessScope,
    ClientGrant,
    ClientPairing,
    GrantLifecycle,
    PairingRequest,
    PairingRequestState,
    PairingScope,
    Permission,
)
from inari.client_trust.store import SqliteClientTrustStore
from inari.drivers import DeviceIdentity, DeviceTransport
from inari.db.migrations import DatabaseMigrator
from inari.physical_execution import (
    DriverExecutionResult,
    DriverOutcome,
    EncryptedExecutionSpool,
    ExecutionOwner,
    ExecutionRejected,
    LeaseLost,
    PhysicalExecution,
    SqliteExecutionLedger,
)
from inari.physical_execution.models import (
    IoPermit,
    PreparationFailed,
    PreparedDeviceWork,
)
from inari.physical_execution._worker import _submit_prepared_work
from inari.print_jobs import OutputEvidence, PrintJobState
from inari.printing.protocols import (
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
)
from inari.printing.renderers import EscPosImageReceiptRenderer
from inari.runtime.store import RuntimeStore
from inari.spool import ArtifactFileStore, SqlActiveAuthorityGuard
from inari.spool.keys import SpoolRootKeyService
from tests import test_durable_spool_admission as spool_support


NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
OWNER = ExecutionOwner("agent-test", 1)
pytestmark = pytest.mark.usefixtures("ample_spool_volume")


@dataclass(frozen=True, slots=True)
class ExecutionFixture:
    database_path: Path
    spool_path: Path
    root_secrets: spool_support.FakeRootSecretStore
    trust_store: SqliteClientTrustStore
    grant: ClientGrant
    ledger: SqliteExecutionLedger


def _jpeg() -> bytes:
    image = Image.new("L", (8, 8), color=255)
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


async def _fixture(
    tmp_path: Path, *, content: bytes | None = None, output_evidence: str = "transport"
) -> ExecutionFixture:
    database_path = spool_support._migrate(tmp_path, output_evidence=output_evidence)
    spool_path = tmp_path / "spool"
    root_secrets = spool_support.FakeRootSecretStore()
    admission = spool_support._store(
        database_path,
        spool_path,
        root_secrets,
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
    )
    await admission.accept(
        spool_support._admission(
            spool_support._jpeg_like_work(content=content or _jpeg())
        )
    )
    trust_store, grant = _seed_client_trust(database_path)
    return ExecutionFixture(
        database_path=database_path,
        spool_path=spool_path,
        root_secrets=root_secrets,
        trust_store=trust_store,
        grant=grant,
        ledger=SqliteExecutionLedger(
            store=RuntimeStore(database_path),
            authority_guard=SqlActiveAuthorityGuard(),
        ),
    )


def _seed_client_trust(
    database_path: Path,
) -> tuple[SqliteClientTrustStore, ClientGrant]:
    scope = PairingScope(
        agent_id="agent-1",
        browser_origin=BoundOrigin("https://odoo.example"),
        agent_endpoint=BoundOrigin("https://agent.example"),
        business=BusinessScope(
            database="odoo",
            company_id="company-1",
            organization_id="org-1",
            site_id="site-1",
            pos_configuration_id="pos-1",
        ),
        audience="inari-agent",
    )
    request = PairingRequest(
        request_id="pairing-request-1",
        scope=scope,
        browser_jwk_thumbprint="A" * 43,
        requested_permissions=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session-nonce-1",
        phrase="amber-river-seven",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    pairing = ClientPairing(
        pairing_id="pairing-1",
        pairing_request_id=request.request_id,
        jwk_thumbprint=request.browser_jwk_thumbprint,
        scope=scope,
        actor_id="actor-1",
        role="device-operator",
        permissions=request.requested_permissions,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    grant = ClientGrant(
        grant_id="grant-1",
        pairing_id=pairing.pairing_id,
        jwk_thumbprint=pairing.jwk_thumbprint,
        scope=scope,
        actor_id=pairing.actor_id,
        role=pairing.role,
        permissions=pairing.permissions,
        authorization_digest="authorization-1",
        generation=1,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    store = SqliteClientTrustStore(database_path)
    store.save_pairing_request(request)
    assert (
        store.transition_pairing_request(
            request.request_id,
            from_states=frozenset({PairingRequestState.PENDING.value}),
            to_state=PairingRequestState.APPROVED.value,
        )
        is not None
    )
    assert store.complete_pairing(
        request=replace(request, state=PairingRequestState.COMPLETED),
        pairing=pairing,
        grant=grant,
        assertion_jti="assertion-1",
        at=NOW,
    )
    return store, grant


def _job(database_path: Path) -> sqlite3.Row:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM public_print_jobs WHERE id = 'job-1'"
        ).fetchone()
    assert row is not None
    return row


@pytest.mark.anyio
async def test_claim_fences_one_active_execution_per_device(tmp_path: Path) -> None:
    fixture = await _fixture(tmp_path)

    first = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    second = fixture.ledger.claim_next(
        ExecutionOwner("other-agent", 1), device_id=None, now=NOW
    )

    assert first is not None
    assert second is None
    with sqlite3.connect(fixture.database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM physical_execution_attempts WHERE phase = 'claimed'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM spool_reservations "
            "WHERE reservation_kind = 'execution_temp' AND state = 'held'"
        ).fetchone() == (1,)


@pytest.mark.anyio
async def test_last_safe_point_rechecks_the_exact_client_grant(tmp_path: Path) -> None:
    fixture = await _fixture(tmp_path)
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None
    fixture.ledger.mark_prepared(claim, now=NOW)
    fixture.trust_store.save_grant(
        replace(fixture.grant, lifecycle=GrantLifecycle.REVOKED)
    )

    with pytest.raises(ExecutionRejected) as error:
        fixture.ledger.mark_io_started(claim, now=NOW + timedelta(seconds=1))

    assert error.value.error_code == "permission_denied"
    assert _job(fixture.database_path)["state"] == "accepted"
    with sqlite3.connect(fixture.database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public_print_job_events "
            "WHERE job_id = 'job-1' AND event_type = 'in_progress'"
        ).fetchone() == (0,)


@pytest.mark.anyio
async def test_managed_work_rechecks_device_authority_without_a_client_grant(
    tmp_path: Path,
) -> None:
    fixture = await _fixture(tmp_path)
    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute("DROP TRIGGER ck_device_work_admissions_identity_immutable")
        connection.execute(
            "UPDATE device_work_admissions SET "
            "scope_kind = 'device_manager', managed_work_id = 'managed-1', "
            "grant_id = NULL, grant_pairing_id = NULL, grant_generation = NULL, "
            "grant_authorization_digest = NULL WHERE id = 'admission-1'"
        )
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None
    assert claim.managed_work_id == "managed-1"
    assert claim.grant_id is None
    fixture.ledger.mark_prepared(claim, now=NOW)
    fixture.trust_store.save_grant(
        replace(fixture.grant, lifecycle=GrantLifecycle.REVOKED)
    )

    permit = fixture.ledger.mark_io_started(claim, now=NOW + timedelta(seconds=1))

    assert permit.job_id == claim.job_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome,evidence,expected",
    [
        (
            DriverOutcome.CONFIRMED,
            OutputEvidence.DEVICE,
            PrintJobState.OUTPUT_CONFIRMED,
        ),
        (
            DriverOutcome.CONFIRMED,
            OutputEvidence.SPOOLER,
            PrintJobState.OUTPUT_CONFIRMED,
        ),
        (
            DriverOutcome.CONFIRMED,
            OutputEvidence.TRANSPORT,
            PrintJobState.OUTPUT_CONFIRMED,
        ),
        (DriverOutcome.CONFIRMED, None, PrintJobState.OUTCOME_UNKNOWN),
        (DriverOutcome.UNKNOWN, OutputEvidence.SPOOLER, PrintJobState.OUTCOME_UNKNOWN),
    ],
)
async def test_output_confirmation_preserves_only_declared_completion_evidence(
    tmp_path: Path,
    outcome,
    evidence,
    expected,
) -> None:
    fixture = await _fixture(tmp_path)
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None
    fixture.ledger.mark_prepared(claim, now=NOW)
    permit = fixture.ledger.mark_io_started(claim, now=NOW)
    fixture.ledger.note_permission_delivered(claim, permit, now=NOW)

    receipt = fixture.ledger.finish(
        claim,
        DriverExecutionResult(
            outcome=outcome,
            evidence=evidence,
            platform_job_id="spooler-1",
        ),
        now=NOW,
    )

    assert receipt.state is expected
    assert _job(fixture.database_path)["confirmation_evidence"] == (
        evidence.value if expected is PrintJobState.OUTPUT_CONFIRMED else None
    )
    with pytest.raises(LeaseLost):
        fixture.ledger.finish(
            claim,
            DriverExecutionResult(
                outcome=DriverOutcome.CONFIRMED,
                evidence=OutputEvidence.DEVICE,
            ),
            now=NOW,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "required,evidence,confirmed",
    [
        ("transport", OutputEvidence.TRANSPORT, True),
        ("transport", OutputEvidence.SPOOLER, True),
        ("transport", OutputEvidence.DEVICE, True),
        ("spooler", OutputEvidence.TRANSPORT, False),
        ("spooler", OutputEvidence.SPOOLER, True),
        ("spooler", OutputEvidence.DEVICE, True),
        ("device", OutputEvidence.TRANSPORT, False),
        ("device", OutputEvidence.SPOOLER, False),
        ("device", OutputEvidence.DEVICE, True),
    ],
)
async def test_completion_must_meet_the_admitted_profile_evidence_level(
    tmp_path: Path, required, evidence, confirmed
) -> None:
    fixture = await _fixture(tmp_path, output_evidence=required)
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None
    fixture.ledger.mark_prepared(claim, now=NOW)
    permit = fixture.ledger.mark_io_started(claim, now=NOW)
    fixture.ledger.note_permission_delivered(claim, permit, now=NOW)

    receipt = fixture.ledger.finish(
        claim,
        DriverExecutionResult(
            outcome=DriverOutcome.CONFIRMED,
            evidence=evidence,
            platform_job_id="spooler-1",
        ),
        now=NOW,
    )

    assert receipt.state is (
        PrintJobState.OUTPUT_CONFIRMED if confirmed else PrintJobState.OUTCOME_UNKNOWN
    )
    job = _job(fixture.database_path)
    assert job["confirmation_evidence"] == (evidence.value if confirmed else None)
    assert job["error_code"] == (None if confirmed else "output_evidence_insufficient")


@pytest.mark.anyio
async def test_recovery_only_takes_stale_work_and_never_retries_after_marker(
    tmp_path: Path,
) -> None:
    fixture = await _fixture(tmp_path)
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None

    live = fixture.ledger.recover(
        ExecutionOwner("other-agent", 1), now=NOW + timedelta(seconds=1)
    )
    assert live.returned_to_accepted == 0

    restarted = fixture.ledger.recover(
        ExecutionOwner(OWNER.owner_id, 2), now=NOW + timedelta(seconds=1)
    )
    assert restarted.returned_to_accepted == 1
    assert _job(fixture.database_path)["state"] == "accepted"

    replacement = fixture.ledger.claim_next(
        ExecutionOwner(OWNER.owner_id, 2),
        device_id=None,
        now=NOW + timedelta(seconds=2),
    )
    assert replacement is not None
    fixture.ledger.mark_prepared(replacement, now=NOW + timedelta(seconds=2))
    fixture.ledger.mark_io_started(replacement, now=NOW + timedelta(seconds=2))
    report = fixture.ledger.recover(
        ExecutionOwner(OWNER.owner_id, 3), now=NOW + timedelta(seconds=3)
    )

    assert report.outcome_unknown == 1
    assert _job(fixture.database_path)["state"] == "outcome_unknown"


@pytest.mark.anyio
async def test_encrypted_spool_persists_and_reuses_derived_printer_bytes(
    tmp_path: Path,
) -> None:
    fixture = await _fixture(tmp_path)
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None
    spool = EncryptedExecutionSpool(
        store=RuntimeStore(fixture.database_path),
        files=ArtifactFileStore(fixture.spool_path),
        root_keys=SpoolRootKeyService(fixture.root_secrets),
        renderer=EscPosImageReceiptRenderer(),
        clock=lambda: NOW,
    )

    first = spool.prepare(claim)
    second = spool.prepare(claim)

    assert first.content == second.content
    assert first.content.startswith(b"\x1b@")
    assert first.content_sha256 == sha256(first.content).digest()
    assert first.normalized_options == claim.normalized_options
    with pytest.raises(PreparationFailed, match="document_policy_rejected"):
        spool.prepare(replace(claim, normalized_options=b'{"dpi":300}'))
    with sqlite3.connect(fixture.database_path) as connection:
        derived = connection.execute(
            "SELECT storage_ref FROM spool_artifacts "
            "WHERE job_id = 'job-1' AND artifact_kind = 'derived_raster'"
        ).fetchall()
    assert len(derived) == 1
    stored = (fixture.spool_path / "objects" / derived[0][0]).read_bytes()
    assert first.content not in stored
    assert not any((fixture.spool_path / "staging").iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize("drained", [False, True])
async def test_options_upgrade_requires_drained_work_and_preserves_outcomes(
    tmp_path: Path,
    drained: bool,
) -> None:
    fixture = await _fixture(tmp_path)
    migrator = DatabaseMigrator(fixture.database_path)
    config = migrator._build_alembic_config()
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None
    if drained:
        fixture.ledger.mark_prepared(claim, now=NOW)
        permit = fixture.ledger.mark_io_started(claim, now=NOW)
        fixture.ledger.note_permission_delivered(claim, permit, now=NOW)
        fixture.ledger.finish(
            claim, DriverExecutionResult(DriverOutcome.UNKNOWN), now=NOW
        )
    before = dict(_job(fixture.database_path))
    command.downgrade(config, "20260904_0013")

    if not drained:
        with pytest.raises(RuntimeError, match="Drain Device Work"):
            migrator.ensure_current()
        with sqlite3.connect(fixture.database_path) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("20260904_0013",)
            assert "normalized_options" not in {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(device_work_admissions)"
                )
            }
        assert dict(_job(fixture.database_path)) == before
        return

    result = migrator.ensure_current()
    assert result.backup_path is not None
    assert result.backup_path.is_file()
    assert dict(_job(fixture.database_path)) == before
    with sqlite3.connect(fixture.database_path) as connection:
        assert connection.execute(
            "SELECT normalized_options, normalized_options_digest FROM device_work_admissions"
        ).fetchone() == (None, claim.normalized_options_digest)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.anyio
async def test_admitted_options_are_immutable(tmp_path: Path) -> None:
    fixture = await _fixture(tmp_path)
    with sqlite3.connect(fixture.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="options are immutable"):
            connection.execute(
                "UPDATE device_work_admissions SET normalized_options = X'7b7d'"
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("media_type", "operation", "content"),
    [
        ("application/pdf", "report_pdf", b"%PDF-1.7\n%%EOF"),
        (
            "application/vnd.zebra-zpl",
            "label_document",
            b"^XA^FDInari^FS^XZ",
        ),
    ],
)
async def test_encrypted_spool_preserves_managed_document_bytes(
    tmp_path: Path,
    media_type: str,
    operation: str,
    content: bytes,
) -> None:
    fixture = await _fixture(tmp_path, content=content)
    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute("DROP TRIGGER ck_device_work_admissions_identity_immutable")
        connection.execute(
            "UPDATE device_work_admissions SET media_type = ?, operation = ? "
            "WHERE id = 'admission-1'",
            (media_type, operation),
        )
    claim = fixture.ledger.claim_next(OWNER, device_id=None, now=NOW)
    assert claim is not None
    spool = EncryptedExecutionSpool(
        store=RuntimeStore(fixture.database_path),
        files=ArtifactFileStore(fixture.spool_path),
        root_keys=SpoolRootKeyService(fixture.root_secrets),
        renderer=EscPosImageReceiptRenderer(),
        clock=lambda: NOW,
    )

    prepared = spool.prepare(claim)

    assert prepared.content == content
    assert prepared.media_type == media_type
    with sqlite3.connect(fixture.database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM spool_artifacts "
            "WHERE job_id = 'job-1' AND artifact_kind = 'derived_raster'"
        ).fetchone() == (0,)


@dataclass(slots=True)
class RecordingDocumentDriver:
    documents: list[tuple[bytes, str, str, int]] = field(default_factory=list)
    raw: list[tuple[bytes, str]] = field(default_factory=list)

    def submit_document_job(
        self,
        printer: PrinterDevice,
        payload: bytes,
        *,
        media_type: str,
        document_name: str,
        dpi: int,
    ) -> PrintJobResult:
        self.documents.append((payload, media_type, document_name, dpi))
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.DOCUMENT,
            bytes_written=len(payload),
            job_id=7,
        )

    def submit_raw_job(
        self, printer: PrinterDevice, payload: bytes, *, document_name: str
    ) -> PrintJobResult:
        self.raw.append((payload, document_name))
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=len(payload),
            job_id=8,
        )


def test_worker_routes_pdf_to_the_platform_document_backend() -> None:
    driver = RecordingDocumentDriver()
    printer = PrinterDevice(
        name="Office",
        driver_key="cups.printers",
        identity=DeviceIdentity(
            transport=DeviceTransport.SPOOLER,
            os_instance_id="test-queue:office",
        ),
        capabilities=PrinterCapabilities(documents=True),
    )
    work = PreparedDeviceWork(
        device_id="device-1",
        driver_key="cups.printers",
        device_name="Office",
        operation="report_pdf",
        media_type="application/pdf",
        content=b"%PDF-1.7\n%%EOF",
        content_sha256=sha256(b"%PDF-1.7\n%%EOF").digest(),
        normalized_options=b'{"dpi":203}',
        deadline=NOW + timedelta(minutes=1),
    )

    result = _submit_prepared_work(driver, printer, work)

    assert result.transport is PrinterTransport.DOCUMENT
    assert driver.documents == [
        (b"%PDF-1.7\n%%EOF", "application/pdf", "Inari Report", 203)
    ]
    assert driver.raw == []


@dataclass(slots=True)
class RecordingSpool:
    released: bool = False

    def prepare(self, claim) -> PreparedDeviceWork:
        content = b"prepared-receipt"
        return PreparedDeviceWork(
            device_id=claim.device_id,
            driver_key=claim.driver_key,
            device_name=claim.device_name,
            operation=claim.operation,
            media_type="application/vnd.inari.escpos",
            content=content,
            content_sha256=sha256(content).digest(),
            normalized_options=claim.normalized_options,
            deadline=claim.expires_at,
        )

    def release(self, claim, receipt) -> None:
        self.released = True


@dataclass(slots=True)
class RecordingPreparedWorker:
    database_path: Path
    executed: bool = False

    async def wait_ready(self) -> None:
        return None

    async def execute(self, permit: IoPermit) -> DriverExecutionResult:
        self.executed = True
        with sqlite3.connect(self.database_path) as connection:
            phase = connection.execute(
                "SELECT phase FROM physical_execution_attempts WHERE attempt_id = ?",
                (permit.attempt_id,),
            ).fetchone()
            state = connection.execute(
                "SELECT state FROM public_print_jobs WHERE id = ?",
                (permit.job_id,),
            ).fetchone()
        assert phase == ("permission_delivered",)
        assert state == ("in_progress",)
        return DriverExecutionResult(
            outcome=DriverOutcome.UNKNOWN,
            evidence=OutputEvidence.SPOOLER,
            platform_job_id="spooler-1",
        )

    async def close(self) -> None:
        return None


@dataclass(slots=True)
class RecordingWorker:
    prepared: RecordingPreparedWorker

    async def prepare(self, work: PreparedDeviceWork) -> RecordingPreparedWorker:
        return self.prepared


@pytest.mark.anyio
async def test_facade_commits_permission_before_the_worker_can_send(
    tmp_path: Path,
) -> None:
    fixture = await _fixture(tmp_path)
    spool = RecordingSpool()
    prepared = RecordingPreparedWorker(fixture.database_path)
    execution = PhysicalExecution(
        ledger=fixture.ledger,
        spool=spool,
        worker=RecordingWorker(prepared),
        clock=lambda: NOW,
    )

    receipt = await execution.run_one(OWNER)

    assert receipt is not None
    assert receipt.state is PrintJobState.OUTCOME_UNKNOWN
    assert prepared.executed
    assert spool.released


@pytest.mark.anyio
async def test_preparation_renews_the_lease_before_device_io(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = await _fixture(tmp_path)
    renewed = asyncio.Event()
    loop = asyncio.get_running_loop()
    clock = [NOW]
    original_renew = fixture.ledger.renew

    def renew(claim, *, now):
        result = original_renew(claim, now=now)
        if now >= NOW + timedelta(seconds=20):
            loop.call_soon_threadsafe(renewed.set)
        return result

    monkeypatch.setattr(fixture.ledger, "renew", renew)
    prepared = RecordingPreparedWorker(fixture.database_path)

    class SlowWorker:
        async def prepare(self, work):
            clock[0] = NOW + timedelta(seconds=20)
            await asyncio.wait_for(renewed.wait(), timeout=1)
            clock[0] = NOW + timedelta(seconds=35)
            return prepared

    execution = PhysicalExecution(
        ledger=fixture.ledger,
        spool=RecordingSpool(),
        worker=SlowWorker(),
        clock=lambda: clock[0],
        heartbeat_seconds=0.001,
    )
    receipt = await execution.run_one(OWNER)
    assert receipt.state is PrintJobState.OUTCOME_UNKNOWN
    assert prepared.executed


@pytest.mark.anyio
async def test_facade_never_calls_the_worker_after_grant_revocation(
    tmp_path: Path,
) -> None:
    fixture = await _fixture(tmp_path)
    fixture.trust_store.save_grant(
        replace(fixture.grant, lifecycle=GrantLifecycle.REVOKED)
    )
    prepared = RecordingPreparedWorker(fixture.database_path)
    execution = PhysicalExecution(
        ledger=fixture.ledger,
        spool=RecordingSpool(),
        worker=RecordingWorker(prepared),
        clock=lambda: NOW,
    )

    receipt = await execution.run_one(OWNER)

    assert receipt is not None
    assert receipt.state is PrintJobState.FAILED
    assert not prepared.executed
    assert _job(fixture.database_path)["error_code"] == "permission_denied"
