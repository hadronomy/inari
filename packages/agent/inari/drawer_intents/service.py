from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..client_trust import AuthorizedRequest, ClientTrustError, Permission
from ..core.exceptions import AgentError
from ..core.failures import DomainFailure, ProblemCode
from ..device_authority import (
    AuthorityError,
    AuthorityErrorCode,
    AuthorityScope,
    CapabilityAdmissionTarget,
    ScopeKind,
    canonical_digest,
    canonical_json_bytes,
)
from .models import (
    DrawerIntentAccepted,
    DrawerIntentPage,
    DrawerIntentRecord,
    DrawerIntentRequest,
    DrawerIntentState,
)
from .ports import CashDrawerPort, DrawerAuthority, DrawerIntentLedger


_IO_TIMEOUT = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class DrawerIntentService:
    """Admit, execute, and reconcile one idempotent cash-drawer pulse."""

    ledger: DrawerIntentLedger
    authority: DrawerAuthority
    drawer: CashDrawerPort
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def submit(
        self,
        request: DrawerIntentRequest,
        authorization: AuthorizedRequest,
    ) -> DrawerIntentAccepted:
        self._require_permission(authorization, Permission.DRAWER)
        if not isinstance(request, DrawerIntentRequest):
            raise DomainFailure(ProblemCode.PAYLOAD_INVALID)
        moment = _utc(self.clock())
        scope = _scope(authorization)
        fingerprint = _fingerprint(request)
        existing = self.ledger.find(
            intent_id=request.intent_id,
            **scope,
            now=moment,
        )
        if existing is not None:
            self._check_replay(existing, fingerprint)
            if existing.state is DrawerIntentState.ACCEPTED:
                return DrawerIntentAccepted(
                    existing,
                    replayed=True,
                    permit=self._authorize(request, authorization, moment),
                )
            if existing.retryable:
                permit = self._authorize(request, authorization, moment)
                rearmed = self.ledger.rearm_before_io(existing.record_id, now=moment)
                if rearmed is not None:
                    return DrawerIntentAccepted(rearmed, replayed=True, permit=permit)
            return DrawerIntentAccepted(existing, replayed=True, permit=None)

        permit = self._authorize(request, authorization, moment)
        admission = self.ledger.admit(
            request,
            authorization=authorization,
            fingerprint=fingerprint,
            now=moment,
        )
        self._check_admission_identity(admission.record, request, authorization)
        self._check_replay(admission.record, fingerprint)
        return DrawerIntentAccepted(
            admission.record,
            replayed=not admission.created,
            permit=(
                permit if admission.record.state is DrawerIntentState.ACCEPTED else None
            ),
        )

    def execute(self, accepted: DrawerIntentAccepted) -> None:
        if accepted.permit is None:
            return
        record_id = accepted.record.record_id
        try:
            self.drawer.ensure_ready(accepted.record.device_id)
            self.authority.check(accepted.permit, now=_utc(self.clock()))
        except (AgentError, AuthorityError):
            self.ledger.mark_terminal(
                record_id,
                expected_state=DrawerIntentState.ACCEPTED,
                state=DrawerIntentState.FAILED,
                error_code="device_unavailable",
                message_key="drawer.device_unavailable",
                now=_utc(self.clock()),
            )
            return

        started = self.ledger.mark_io_started(record_id, now=_utc(self.clock()))
        if started is None:
            return
        try:
            result = self.drawer.open_cash_drawer(started.device_id)
        except Exception:
            self.ledger.mark_terminal(
                record_id,
                expected_state=DrawerIntentState.IN_PROGRESS,
                state=DrawerIntentState.OUTCOME_UNKNOWN,
                error_code="outcome_unknown",
                message_key="drawer.outcome_unknown",
                now=_utc(self.clock()),
            )
            return
        self.ledger.mark_succeeded(record_id, result=result, now=_utc(self.clock()))

    def query(
        self,
        intent_ids: tuple[str, ...],
        authorization: AuthorizedRequest,
    ) -> DrawerIntentPage:
        self._require_permission(authorization, Permission.JOBS_READ)
        moment = _utc(self.clock())
        self.ledger.expire_stale_in_progress(
            cutoff=moment - _IO_TIMEOUT,
            now=moment,
        )
        requested = tuple(dict.fromkeys(intent_ids))
        records = self.ledger.query(
            intent_ids=requested,
            **_scope(authorization),
            now=moment,
        )
        by_id = {record.intent_id: record for record in records}
        return DrawerIntentPage(
            intents=tuple(
                by_id[intent_id] for intent_id in requested if intent_id in by_id
            ),
            missing_intent_ids=tuple(
                intent_id for intent_id in requested if intent_id not in by_id
            ),
        )

    def _authorize(
        self,
        request: DrawerIntentRequest,
        authorization: AuthorizedRequest,
        moment: datetime,
    ):
        business = authorization.grant.scope.business
        assert business.pos_configuration_id is not None
        try:
            return self.authority.authorize(
                CapabilityAdmissionTarget(
                    scope=AuthorityScope(
                        database=business.database,
                        organization_id=business.organization_id,
                        site_id=business.site_id,
                        kind=ScopeKind.POS_CONFIGURATION,
                        pos_configuration_id=business.pos_configuration_id,
                    ),
                    purpose="pos_cash_drawer",
                    device_id=request.device_id,
                    binding_revision_id=request.binding_revision_id,
                    operation="open_cash_drawer",
                    media_type="application/inari-drawer-intent+json",
                    contract_major=1,
                    options_digest=canonical_digest({}),
                    requested_expires_at=moment + _IO_TIMEOUT,
                ),
                now=moment,
            )
        except AuthorityError as error:
            raise DomainFailure(_authority_problem(error.code)) from error

    @staticmethod
    def _check_replay(record: DrawerIntentRecord, fingerprint: bytes) -> None:
        if record.fingerprint != fingerprint:
            raise DomainFailure(ProblemCode.IDEMPOTENCY_CONFLICT)

    @staticmethod
    def _check_admission_identity(
        record: DrawerIntentRecord,
        request: DrawerIntentRequest,
        authorization: AuthorizedRequest,
    ) -> None:
        if (
            record.intent_id != request.intent_id
            or record.paired_client_id != authorization.grant.pairing_id
        ):
            raise DomainFailure(ProblemCode.IDEMPOTENCY_CONFLICT)

    @staticmethod
    def _require_permission(
        authorization: AuthorizedRequest, permission: Permission
    ) -> None:
        try:
            authorization.require(permission)
        except ClientTrustError as error:
            raise DomainFailure(ProblemCode.PERMISSION_DENIED) from error


def _scope(authorization: AuthorizedRequest) -> dict[str, str]:
    business = authorization.grant.scope.business
    if business.pos_configuration_id is None:
        raise DomainFailure(ProblemCode.BINDING_REQUIRED)
    return {
        "database": business.database,
        "organization_id": business.organization_id,
        "site_id": business.site_id,
        "pos_configuration_id": business.pos_configuration_id,
        "paired_client_id": authorization.grant.pairing_id,
    }


def _fingerprint(request: DrawerIntentRequest) -> bytes:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "contract_major": 1,
                "drawer_intent_id": request.intent_id,
                "device_id": request.device_id,
                "binding_revision_id": request.binding_revision_id,
                "pos_session_id": request.pos_session_id,
                "action_sequence": request.action_sequence,
                "reason": request.reason.value,
            }
        )
    ).digest()


def _authority_problem(code: AuthorityErrorCode) -> ProblemCode:
    return {
        AuthorityErrorCode.INVALID_REQUEST: ProblemCode.PAYLOAD_INVALID,
        AuthorityErrorCode.SCOPE_MISMATCH: ProblemCode.PERMISSION_DENIED,
        AuthorityErrorCode.NOT_FOUND: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.SIGNATURE_INVALID: ProblemCode.SERVICE_UNAVAILABLE,
        AuthorityErrorCode.SIGNER_PURPOSE_MISMATCH: ProblemCode.SERVICE_UNAVAILABLE,
        AuthorityErrorCode.REVOKED: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.EXPIRED: ProblemCode.EXPIRED,
        AuthorityErrorCode.GRAPH_MISMATCH: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.CERTIFICATION_REQUIRED: ProblemCode.CERTIFICATION_REQUIRED,
        AuthorityErrorCode.TEST_REQUIRED: ProblemCode.CERTIFICATION_REQUIRED,
        AuthorityErrorCode.OBSERVATION_UNAVAILABLE: ProblemCode.DEVICE_UNAVAILABLE,
        AuthorityErrorCode.OBSERVATION_DRIFT: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.DEVICE_NOT_READY: ProblemCode.DEVICE_UNAVAILABLE,
        AuthorityErrorCode.AUTHORITY_UNAVAILABLE: ProblemCode.SERVICE_UNAVAILABLE,
    }[code]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Drawer Intent clock must return a timezone-aware value")
    return value.astimezone(UTC)


__all__ = ["DrawerIntentService"]
