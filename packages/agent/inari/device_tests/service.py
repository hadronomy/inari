from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import logging
from uuid import uuid4

from ..client_trust import AuthorizedRequest, ClientTrustError, Permission
from ..core.failures import DomainFailure, ProblemCode
from ..device_authority import (
    AuthorityError,
    AuthorityStatus,
    AuthorityScope,
    CapabilityAdmissionTarget,
    DeviceCapabilityAuthority,
    ScopeKind,
    SqliteDeviceAuthorityReader,
    canonical_digest,
)
from ..device_authority.authority import device_test_payload
from ..device_authority.models import (
    DeviceTestEvidence,
    DeviceTestResult,
    OutputEvidence,
    SignedDeviceTestEvidence,
    SignerPurpose,
    SignerState,
    RevocationSubjectKind,
)
from ..physical_execution.models import (
    DriverExecutionResult,
    DriverOutcome,
    PreparedDeviceWork,
)
from ..physical_execution.ports import DeviceWorker
from ..printing.renderers.image_escpos_renderer import EscPosImageReceiptRenderer
from ..runtime.devices.service import DeviceCatalog
from .models import (
    DeviceTestAccepted,
    DeviceTestRecord,
    DeviceTestRequest,
    PhysicalCheckAnswer,
    TestState,
    physical_checks,
)
from ..printing.receipt_pattern import (
    CODE_VALUE,
    PATTERN_DIGEST,
    PATTERN_VERSION,
    prepared_receipt,
    receipt_image,
)
from .signing import DeviceTestSigningKey
from .sqlite import SqliteDeviceTestLedger, parse_time, scope_values, timestamp

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeviceTestService:
    """Execute a fixed receipt and sign results only after physical answers."""

    ledger: SqliteDeviceTestLedger
    authority: DeviceCapabilityAuthority
    projections: SqliteDeviceAuthorityReader
    devices: DeviceCatalog
    renderer: EscPosImageReceiptRenderer
    worker: DeviceWorker
    signing_key: DeviceTestSigningKey
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def submit(
        self, request: DeviceTestRequest, authorization: AuthorizedRequest
    ) -> DeviceTestAccepted:
        self._require(authorization)
        now = self.clock()
        fingerprint = bytes.fromhex(
            canonical_digest(
                {
                    "contract_major": 1,
                    "test_id": request.test_id,
                    "device_id": request.device_id,
                    "binding_revision_id": request.binding_revision_id,
                    "test_pattern_digest": PATTERN_DIGEST,
                }
            )
        )
        existing = self.ledger.find(request.test_id, authorization, now=now)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise DomainFailure(ProblemCode.IDEMPOTENCY_CONFLICT)
            return DeviceTestAccepted(existing, True, None, authorization)
        await self.devices.refresh()
        now = self.clock()
        business = authorization.grant.scope.business
        target = CapabilityAdmissionTarget(
            scope=AuthorityScope(
                database=business.database,
                organization_id=business.organization_id,
                site_id=business.site_id,
                kind=ScopeKind.POS_CONFIGURATION,
                pos_configuration_id=business.pos_configuration_id,
            ),
            purpose="pos_receipt",
            device_id=request.device_id,
            binding_revision_id=request.binding_revision_id,
            operation="receipt_image",
            media_type="image/jpeg",
            contract_major=1,
            options_digest=canonical_digest({}),
            requested_expires_at=now + timedelta(seconds=30),
        )
        try:
            permit = self.authority.authorize_test(target, now=now)
            self._check_signer(permit.authorization.revision.revision.revision_id, now)
        except AuthorityError as error:
            raise DomainFailure(ProblemCode.CAPABILITY_CHANGED) from error
        if len(receipt_image()) > permit.authorization.capability.max_payload_bytes:
            raise DomainFailure(ProblemCode.PAYLOAD_INVALID)
        if (
            len(prepared_receipt(self.renderer))
            > permit.authorization.capability.max_payload_bytes
        ):
            raise DomainFailure(ProblemCode.PAYLOAD_INVALID)
        record, created = self.ledger.admit(
            request,
            authorization,
            fingerprint,
            now=now,
            deadline=permit.authorization.expires_at,
        )
        return DeviceTestAccepted(
            record, not created, permit if created else None, authorization
        )

    async def execute(self, accepted: DeviceTestAccepted) -> None:
        permit = accepted.permit
        if permit is None:
            return
        record = accepted.record
        claim = self.ledger.claim_worker(
            record, accepted.authorization, now=self.clock()
        )
        if claim is None:
            return
        worker = None
        marker = None
        result = DriverExecutionResult(
            outcome=DriverOutcome.UNKNOWN, error_code="outcome_unknown"
        )
        error_code = "device_unavailable"
        try:
            content = prepared_receipt(self.renderer)
            device = self.devices.get_device(record.device_id)
            if (
                device is None
                or device.driver_key != permit.authorization.profile.profile.driver_id
            ):
                raise ValueError("The Device Test driver changed.")
            worker = await self.worker.prepare(
                PreparedDeviceWork(
                    device_id=device.id,
                    driver_key=device.driver_key,
                    device_name=device.name,
                    operation="receipt_image",
                    media_type="application/vnd.inari.escpos",
                    content=content,
                    content_sha256=sha256(content).digest(),
                    normalized_options=b"{}",
                    deadline=record.io_deadline,
                )
            )
            await worker.wait_ready()
            await self.devices.refresh()
            now = self.clock()

            def check():
                current = self.authority.check_test(permit, now=now)
                self._check_signer(current.revision.revision.revision_id, now)
                return current

            marker = self.ledger.mark_io_started(
                claim, accepted.authorization, check, now=now
            )
            if marker is None:
                if now >= record.io_deadline:
                    error_code = "execution_deadline"
                return
            result = await worker.execute(marker)
        except asyncio.CancelledError:
            error_code = "execution_canceled"
            raise
        except AuthorityError:
            error_code = "authority_changed"
        except DomainFailure as error:
            error_code = error.code.value
        except Exception:
            logger.exception("Device Test execution failed: %s", record.record_id)
        finally:
            if worker is not None:
                try:
                    await worker.close()
                except (Exception, asyncio.CancelledError):
                    self.ledger.mark_worker_stop_failed(claim)
                    raise
            if marker is not None:
                self.ledger.finish_io(claim, result, now=self.clock())
            else:
                self.ledger.fail_before_io(
                    claim, now=self.clock(), error_code=error_code
                )

    def get(self, test_id: str, authorization: AuthorizedRequest) -> DeviceTestRecord:
        self._require(authorization)
        record = self.ledger.find(test_id, authorization, now=self.clock())
        if record is None:
            raise DomainFailure(ProblemCode.RESOURCE_NOT_FOUND)
        return record

    def finalize(
        self, test_id: str, checks: dict[str, str], authorization: AuthorizedRequest
    ) -> DeviceTestRecord:
        self._require(authorization)
        try:
            answers = physical_checks(checks)
        except ValueError as error:
            raise DomainFailure(ProblemCode.PAYLOAD_INVALID) from error
        now = self.clock()

        def sign(
            record: DeviceTestRecord,
        ) -> tuple[dict[str, object], SignedDeviceTestEvidence | None]:
            graph = record.graph
            evidence = None
            outcome = self._outcome(record, answers)
            state = self.projections.read_authority_state()
            if state is None or state.status is not AuthorityStatus.READY:
                raise DomainFailure(ProblemCode.SERVICE_UNAVAILABLE)
            if graph is not None:
                self._check_recorded_graph(
                    record, graph, state.current_revision.revision.revision_id
                )
            revision_id = (
                str(graph["authority_revision_id"])
                if graph
                else state.current_revision.revision.revision_id
            )
            signer = self._check_signer(revision_id, now)
            if graph is not None and record.output_evidence is not None:
                binding = graph["binding"]
                assert isinstance(binding, dict)
                expiry = (
                    parse_time(str(graph["valid_until"]))
                    if graph["valid_until"]
                    else None
                )
                if expiry is not None and expiry <= now:
                    raise DomainFailure(ProblemCode.EXPIRED)
                expiry = (
                    min(expiry, signer.not_after)
                    if expiry and signer.not_after
                    else expiry or signer.not_after
                )
                evidence = self.signing_key.sign_evidence(
                    DeviceTestEvidence(
                        evidence_id=uuid4().hex,
                        revision_id=record.binding_revision_id,
                        device_id=record.device_id,
                        device_identity_digest=str(binding["device_identity_digest"]),
                        capability_id=str(binding["capability_id"]),
                        driver_profile_digest=str(binding["driver_profile_digest"]),
                        matrix_row_id=str(binding["matrix_row_id"]),
                        output_evidence=OutputEvidence(record.output_evidence),
                        result=outcome,
                        test_pattern_digest=PATTERN_DIGEST,
                        tested_at=now,
                        valid_until=expiry,
                    )
                )
            result = {
                "contract_major": 1,
                "device_test_id": record.test_id,
                "record_id": record.record_id,
                **scope_values(authorization),
                "device_id": record.device_id,
                "binding_revision_id": record.binding_revision_id,
                "pattern_version": PATTERN_VERSION,
                "test_pattern_digest": PATTERN_DIGEST,
                "code_value": CODE_VALUE,
                "checks": answers,
                "outcome": outcome.value,
                "graph": graph,
                "accepted_at": timestamp(record.accepted_at),
                "started_at": timestamp(record.started_at)
                if record.started_at
                else None,
                "finished_at": timestamp(now),
                "output_evidence": record.output_evidence,
                "platform_job_id": record.platform_job_id,
                "error_code": record.error_code,
                "evidence": {
                    "evidence": device_test_payload(evidence.evidence),
                    "digest": evidence.digest,
                    "signer_key_id": evidence.signer_key_id,
                    "signature": evidence.signature.hex(),
                }
                if evidence
                else None,
            }
            return self.signing_key.sign_result(result), evidence

        return self.ledger.finalize(test_id, authorization, answers, sign, now=now)

    def _check_recorded_graph(
        self,
        record: DeviceTestRecord,
        graph: dict[str, object],
        current_revision_id: str,
    ) -> None:
        binding = graph["binding"]
        assert isinstance(binding, dict)
        subjects = (
            (
                RevocationSubjectKind.AUTHORITY_REVISION,
                graph["authority_revision_id"],
                graph["authority_revision_digest"],
            ),
            (
                RevocationSubjectKind.BINDING_REVISION,
                record.binding_revision_id,
                graph["binding_digest"],
            ),
            (
                RevocationSubjectKind.DRIVER_PROFILE,
                graph["profile_id"],
                graph["profile_digest"],
            ),
            (
                RevocationSubjectKind.CERTIFICATION_MATRIX_ROW,
                binding["matrix_row_id"],
                graph["matrix_digest"],
            ),
        )
        if any(
            self.projections.is_revoked(kind, str(subject), str(digest))
            for kind, subject, digest in subjects
        ):
            raise DomainFailure(ProblemCode.CAPABILITY_CHANGED)
        manifest = self.projections.read_manifest(current_revision_id)
        if manifest is None or not (
            any(item.digest == graph["binding_digest"] for item in manifest.bindings)
            and any(
                item.digest == graph["profile_digest"] for item in manifest.profiles
            )
            and any(
                item.digest == graph["matrix_digest"]
                for item in manifest.certification_rows
            )
        ):
            raise DomainFailure(ProblemCode.CAPABILITY_CHANGED)

    def _check_signer(self, revision_id: str, now: datetime):
        signer = self.projections.read_signer(
            self.signing_key.key_id(), SignerPurpose.DEVICE_TEST_EVIDENCE
        )
        manifest = self.projections.read_manifest(revision_id)
        state = self.projections.read_authority_state()
        current_manifest = (
            self.projections.read_manifest(state.current_revision.revision.revision_id)
            if state is not None
            else None
        )
        if (
            signer is None
            or signer.public_key != self.signing_key.public_key()
            or manifest is None
            or signer not in manifest.signers
            or current_manifest is None
            or signer not in current_manifest.signers
        ):
            raise DomainFailure(ProblemCode.CERTIFICATION_REQUIRED)
        assert state is not None
        revision = state.current_revision.revision
        if (
            state.status is not AuthorityStatus.READY
            or now < revision.effective_at
            or revision.expires_at is None
            or now >= revision.expires_at
        ):
            raise DomainFailure(ProblemCode.EXPIRED)
        if (
            signer.state is not SignerState.ACTIVE
            or now < signer.not_before
            or (signer.not_after is not None and now >= signer.not_after)
        ):
            raise DomainFailure(ProblemCode.CERTIFICATION_REQUIRED)
        return signer

    def _require(self, authorization: AuthorizedRequest) -> None:
        try:
            authorization.require(Permission.DEVICE_TEST)
            authorization.grant.active_at(self.clock())
        except ClientTrustError as error:
            raise DomainFailure(ProblemCode.PERMISSION_DENIED) from error
        if authorization.grant.role != "device_manager":
            raise DomainFailure(ProblemCode.PERMISSION_DENIED)
        scope_values(authorization)

    @staticmethod
    def _outcome(record: DeviceTestRecord, checks: dict[str, str]) -> DeviceTestResult:
        ranks = {"transport": 1, "spooler": 2, "device": 3}
        if (
            record.state is not TestState.AWAITING_CHECKS
            or record.graph is None
            or ranks.get(record.output_evidence or "", 0)
            < ranks[str(record.graph["required_output_evidence"])]
        ):
            return DeviceTestResult.FAILED_ENVIRONMENT
        if PhysicalCheckAnswer.NOT_RUN.value in checks.values():
            return DeviceTestResult.FAILED_ENVIRONMENT
        if PhysicalCheckAnswer.INCORRECT.value in checks.values():
            return DeviceTestResult.FAILED_CONTRACT
        return DeviceTestResult.PASSED
