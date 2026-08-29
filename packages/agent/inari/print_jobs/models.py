from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import ClassVar, Self, TypeAlias


MAX_IDENTIFIER_LENGTH = 256
ACTIVE_RECONCILIATION_PERIOD = timedelta(hours=24)
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_ERROR_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MESSAGE_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_.]{0,127}\Z")
_SHA256_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class PrintJobState(StrEnum):
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    OUTPUT_CONFIRMED = "output_confirmed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    EXPIRED = "expired"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in {
            self.OUTPUT_CONFIRMED,
            self.FAILED,
            self.OUTCOME_UNKNOWN,
            self.EXPIRED,
            self.CANCELED,
        }


class PrintOriginKind(StrEnum):
    POS = "pos"
    PREPARATION = "preparation"
    REPORT = "report"


class PrintJobScopeKind(StrEnum):
    PAIRED_CLIENT = "paired_client"
    DEVICE_MANAGER = "device_manager"


class OutputEvidence(StrEnum):
    DEVICE = "device"
    SPOOLER = "spooler"
    TRANSPORT = "transport"


class LateEvidenceOutcome(StrEnum):
    CONFIRMED = "later_confirmed"
    FAILED = "later_failed"


@dataclass(frozen=True, slots=True)
class PosPrintOrigin:
    organization_id: str
    site_id: str
    database: str
    paired_client_id: str
    pos_configuration_id: str
    pos_session_id: str
    offline_order_id: str
    server_order_id: str | None
    document_kind: str
    content_revision: str

    kind: ClassVar[PrintOriginKind] = PrintOriginKind.POS

    def __post_init__(self) -> None:
        _validate_pos_origin(self)


@dataclass(frozen=True, slots=True)
class PreparationPrintOrigin:
    organization_id: str
    site_id: str
    database: str
    paired_client_id: str
    pos_configuration_id: str
    pos_session_id: str
    offline_order_id: str
    server_order_id: str | None
    document_kind: str
    content_revision: str
    segment_kind: str
    segment_index: int
    preparation_revision: str

    kind: ClassVar[PrintOriginKind] = PrintOriginKind.PREPARATION

    def __post_init__(self) -> None:
        _validate_pos_origin(self)
        _validate_identifier("segment_kind", self.segment_kind)
        if self.segment_index < 0:
            raise ValueError("Preparation segment_index must be nonnegative.")
        _validate_identifier("preparation_revision", self.preparation_revision)


@dataclass(frozen=True, slots=True)
class ReportPrintOrigin:
    organization_id: str
    site_id: str
    database: str
    company_id: str
    report_binding_id: str
    report_route: str
    report_action: str
    source_model: str
    record_ids: tuple[str, ...]
    wizard_input_digest: str | None
    rendered_document_index: int

    kind: ClassVar[PrintOriginKind] = PrintOriginKind.REPORT

    def __post_init__(self) -> None:
        for name in (
            "organization_id",
            "site_id",
            "database",
            "company_id",
            "report_binding_id",
            "report_route",
            "report_action",
            "source_model",
        ):
            _validate_identifier(name, getattr(self, name))
        if self.rendered_document_index < 0:
            raise ValueError("Report rendered_document_index must be nonnegative.")
        has_records = bool(self.record_ids)
        has_wizard = self.wizard_input_digest is not None
        if has_records == has_wizard:
            raise ValueError(
                "A Report Print Origin requires exactly one of record_ids or "
                "wizard_input_digest."
            )
        if type(self.record_ids) is not tuple:
            raise TypeError("Report record_ids must be an immutable tuple.")
        if len(self.record_ids) > 100:
            raise ValueError("A Report Print Origin cannot contain more than 100 records.")
        for record_id in self.record_ids:
            _validate_identifier("record_id", record_id)
        if self.wizard_input_digest is not None and (
            not isinstance(self.wizard_input_digest, str)
            or not _SHA256_DIGEST_PATTERN.fullmatch(self.wizard_input_digest)
        ):
            raise ValueError("wizard_input_digest must be a lower-case SHA-256 digest.")


PrintOrigin: TypeAlias = PosPrintOrigin | PreparationPrintOrigin | ReportPrintOrigin


@dataclass(frozen=True, slots=True)
class PairedClientScope:
    organization_id: str
    site_id: str
    pos_configuration_id: str
    paired_client_id: str

    kind: ClassVar[PrintJobScopeKind] = PrintJobScopeKind.PAIRED_CLIENT

    def __post_init__(self) -> None:
        for name in (
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "paired_client_id",
        ):
            _validate_identifier(name, getattr(self, name))

    def allows(self, job: PrintJob) -> bool:
        if not isinstance(job, PrintJob):
            return False
        origin = job.origin
        return (
            _is_pos_origin(origin)
            and origin.organization_id == self.organization_id
            and origin.site_id == self.site_id
            and origin.pos_configuration_id == self.pos_configuration_id
            and origin.paired_client_id == self.paired_client_id
        )


@dataclass(frozen=True, slots=True)
class SiteManagerScope:
    organization_id: str
    site_id: str
    pos_configuration_id: str | None = None
    paired_client_id: str | None = None

    kind: ClassVar[PrintJobScopeKind] = PrintJobScopeKind.DEVICE_MANAGER

    def __post_init__(self) -> None:
        _validate_identifier("organization_id", self.organization_id)
        _validate_identifier("site_id", self.site_id)
        if (self.pos_configuration_id is None) != (self.paired_client_id is None):
            raise ValueError(
                "A narrowed Device Manager scope requires both POS configuration "
                "and paired client identifiers."
            )
        if self.pos_configuration_id is not None:
            _validate_identifier("pos_configuration_id", self.pos_configuration_id)
            assert self.paired_client_id is not None
            _validate_identifier("paired_client_id", self.paired_client_id)

    def allows(self, job: PrintJob) -> bool:
        if not isinstance(job, PrintJob):
            return False
        origin = job.origin
        if (
            origin.organization_id != self.organization_id
            or origin.site_id != self.site_id
        ):
            return False
        if self.pos_configuration_id is None:
            return True
        return (
            _is_pos_origin(origin)
            and origin.pos_configuration_id == self.pos_configuration_id
            and origin.paired_client_id == self.paired_client_id
        )


PrintJobScope: TypeAlias = PairedClientScope | SiteManagerScope


@dataclass(frozen=True, slots=True)
class PayloadFingerprint:
    value: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.value, bytes) or len(self.value) != 32:
            raise ValueError("A SHA-256 Payload Fingerprint must contain exactly 32 bytes.")


@dataclass(frozen=True, slots=True)
class FirstIoMarker:
    """Proof that the first Device I/O marker has a durable journal position."""

    marker_id: str
    job_id: str
    committed_at: datetime
    durable_sequence: int

    def __post_init__(self) -> None:
        _validate_identifier("marker_id", self.marker_id)
        _validate_identifier("job_id", self.job_id)
        if type(self.durable_sequence) is not int or self.durable_sequence < 1:
            raise ValueError("FirstIoMarker durable_sequence must be positive.")
        object.__setattr__(self, "committed_at", _utc_required(self.committed_at))


@dataclass(frozen=True, slots=True)
class PrintJob:
    job_id: str
    intent_id: str
    device_id: str
    origin: PrintOrigin
    state: PrintJobState
    state_version: int
    accepted_at: datetime
    expires_at: datetime
    retryable: bool
    contract_version: str
    managed_work_id: str | None = None
    started_at: datetime | None = None
    terminal_at: datetime | None = None
    error_code: str | None = None
    message_key: str | None = None
    confirmation_evidence: OutputEvidence | str | None = None

    def __post_init__(self) -> None:
        for name in ("job_id", "intent_id", "device_id", "contract_version"):
            _validate_identifier(name, getattr(self, name))
        if self.managed_work_id is not None:
            _validate_identifier("managed_work_id", self.managed_work_id)
        if not _is_print_origin(self.origin):
            raise TypeError("Print Job origin must be a supported Print Origin.")
        if type(self.state_version) is not int or self.state_version < 1:
            raise ValueError("Print Job state_version must be positive.")
        if type(self.retryable) is not bool:
            raise TypeError("Print Job retryable must be a boolean.")
        accepted_at = _utc_required(self.accepted_at)
        expires_at = _utc_required(self.expires_at)
        started_at = _utc_optional(self.started_at)
        terminal_at = _utc_optional(self.terminal_at)
        if expires_at <= accepted_at:
            raise ValueError("Print Job expires_at must be after accepted_at.")
        if started_at is not None and started_at < accepted_at:
            raise ValueError("Print Job started_at cannot precede accepted_at.")
        if terminal_at is not None and terminal_at < accepted_at:
            raise ValueError("Print Job terminal_at cannot precede accepted_at.")
        if terminal_at is not None and started_at is not None and terminal_at < started_at:
            raise ValueError("Print Job terminal_at cannot precede started_at.")
        state = PrintJobState(self.state)
        evidence = (
            None
            if self.confirmation_evidence is None
            else OutputEvidence(self.confirmation_evidence)
        )
        _validate_lifecycle(
            state=state,
            started_at=started_at,
            terminal_at=terminal_at,
            error_code=self.error_code,
            message_key=self.message_key,
            confirmation_evidence=evidence,
        )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "accepted_at", accepted_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "terminal_at", terminal_at)
        object.__setattr__(self, "confirmation_evidence", evidence)


_TRANSITIONS = {
    PrintJobState.ACCEPTED: frozenset(
        {
            PrintJobState.IN_PROGRESS,
            PrintJobState.FAILED,
            PrintJobState.EXPIRED,
            PrintJobState.CANCELED,
        }
    ),
    PrintJobState.IN_PROGRESS: frozenset(
        {
            PrintJobState.OUTPUT_CONFIRMED,
            PrintJobState.FAILED,
            PrintJobState.OUTCOME_UNKNOWN,
        }
    ),
    PrintJobState.OUTCOME_UNKNOWN: frozenset(
        {PrintJobState.OUTPUT_CONFIRMED, PrintJobState.FAILED}
    ),
    PrintJobState.OUTPUT_CONFIRMED: frozenset(),
    PrintJobState.FAILED: frozenset(),
    PrintJobState.EXPIRED: frozenset(),
    PrintJobState.CANCELED: frozenset(),
}


def can_transition(current: PrintJobState, target: PrintJobState) -> bool:
    return PrintJobState(target) in _TRANSITIONS[PrintJobState(current)]


def transition(
    job: PrintJob,
    target: PrintJobState,
    *,
    at: datetime,
    first_io_marker: FirstIoMarker | None = None,
    confirmation_evidence: OutputEvidence | str | None = None,
    error_code: str | None = None,
    message_key: str | None = None,
    retryable: bool | None = None,
) -> PrintJob:
    target = PrintJobState(target)
    if not can_transition(job.state, target):
        raise ValueError(
            f"invalid Print Job transition: {job.state.value} -> {target.value}"
        )
    at = _utc_required(at)
    previous_at = job.terminal_at or job.started_at or job.accepted_at
    if at < previous_at:
        raise ValueError("Print Job transition time cannot move backwards.")
    if target is PrintJobState.IN_PROGRESS:
        _validate_first_io_marker(job, first_io_marker, at)
        if at > job.expires_at:
            raise ValueError("A Print Job cannot enter in_progress after expires_at.")
    elif first_io_marker is not None:
        raise ValueError("FirstIoMarker is valid only for the in_progress transition.")
    if target is PrintJobState.EXPIRED and at < job.expires_at:
        raise ValueError("A Print Job cannot expire before expires_at.")
    if job.state is PrintJobState.OUTCOME_UNKNOWN:
        assert job.terminal_at is not None
        if at > job.terminal_at + ACTIVE_RECONCILIATION_PERIOD:
            raise ValueError(
                "An outcome_unknown Print Job can improve only during the 24-hour "
                "active reconciliation period."
            )
    if target is PrintJobState.OUTPUT_CONFIRMED and confirmation_evidence is None:
        raise ValueError("output_confirmed requires confirmation_evidence.")
    if target in {PrintJobState.FAILED, PrintJobState.OUTCOME_UNKNOWN}:
        _validate_failure(error_code, message_key)
    started_at = at if target is PrintJobState.IN_PROGRESS else job.started_at
    return PrintJob(
        job_id=job.job_id,
        intent_id=job.intent_id,
        device_id=job.device_id,
        origin=job.origin,
        state=target,
        state_version=job.state_version + 1,
        accepted_at=job.accepted_at,
        expires_at=job.expires_at,
        retryable=job.retryable if retryable is None else retryable,
        contract_version=job.contract_version,
        managed_work_id=job.managed_work_id,
        started_at=started_at,
        terminal_at=at if target.terminal else None,
        error_code=error_code,
        message_key=message_key,
        confirmation_evidence=(
            confirmation_evidence
            if target is PrintJobState.OUTPUT_CONFIRMED
            else None
        ),
    )


@dataclass(frozen=True, slots=True)
class LateEvidenceAnnotation:
    job_id: str
    observed_state_version: int
    outcome: LateEvidenceOutcome
    observed_at: datetime
    confirmation_evidence: OutputEvidence | None = None
    error_code: str | None = None
    message_key: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier("job_id", self.job_id)
        if (
            type(self.observed_state_version) is not int
            or self.observed_state_version < 1
        ):
            raise ValueError("observed_state_version must be positive.")
        object.__setattr__(self, "observed_at", _utc_required(self.observed_at))
        if self.outcome is LateEvidenceOutcome.CONFIRMED:
            if self.confirmation_evidence is None:
                raise ValueError("Later confirmed evidence requires Output Evidence.")
            if self.error_code is not None or self.message_key is not None:
                raise ValueError("Later confirmed evidence cannot contain an error.")
        else:
            if self.confirmation_evidence is not None:
                raise ValueError("Later failed evidence cannot confirm output.")
            _validate_failure(self.error_code, self.message_key)


def annotate_late_evidence(
    job: PrintJob,
    *,
    outcome: LateEvidenceOutcome,
    observed_at: datetime,
    confirmation_evidence: OutputEvidence | None = None,
    error_code: str | None = None,
    message_key: str | None = None,
) -> LateEvidenceAnnotation:
    if job.state is not PrintJobState.OUTCOME_UNKNOWN or job.terminal_at is None:
        raise ValueError("Late evidence requires an outcome_unknown Print Job.")
    observed_at = _utc_required(observed_at)
    if observed_at <= job.terminal_at + ACTIVE_RECONCILIATION_PERIOD:
        raise ValueError(
            "Use a state transition during the 24-hour active reconciliation period."
        )
    return LateEvidenceAnnotation(
        job_id=job.job_id,
        observed_state_version=job.state_version,
        outcome=LateEvidenceOutcome(outcome),
        observed_at=observed_at,
        confirmation_evidence=confirmation_evidence,
        error_code=error_code,
        message_key=message_key,
    )


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    job: PrintJob
    replayed: bool


@dataclass(frozen=True, slots=True)
class PrintJobQuery:
    job_ids: tuple[str, ...]
    scope: PrintJobScope

    @classmethod
    def from_ids(
        cls, job_ids: list[str] | tuple[str, ...], *, scope: PrintJobScope
    ) -> Self:
        if not job_ids:
            raise ValueError("A Print Job query requires at least one identifier.")
        if len(job_ids) > 100:
            raise ValueError("A Print Job query cannot contain more than 100 identifiers.")
        for job_id in job_ids:
            _validate_identifier("job_id", job_id)
        return cls(job_ids=tuple(dict.fromkeys(job_ids)), scope=scope)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, (PairedClientScope, SiteManagerScope)):
            raise TypeError("Print Job query scope must be an authorized closed scope.")
        if type(self.job_ids) is not tuple:
            raise TypeError("A Print Job query uses an immutable tuple of identifiers.")
        if not self.job_ids:
            raise ValueError("A Print Job query requires at least one identifier.")
        if len(self.job_ids) > 100:
            raise ValueError("A Print Job query cannot contain more than 100 identifiers.")
        for job_id in self.job_ids:
            _validate_identifier("job_id", job_id)


@dataclass(frozen=True, slots=True)
class PrintJobPage:
    jobs: tuple[PrintJob, ...]
    missing_job_ids: tuple[str, ...]
    high_water_mark: int

    def __post_init__(self) -> None:
        if type(self.jobs) is not tuple or type(self.missing_job_ids) is not tuple:
            raise TypeError("A Print Job page uses immutable tuples.")
        if type(self.high_water_mark) is not int or self.high_water_mark < 0:
            raise ValueError("Agent high_water_mark must be nonnegative.")
        if len(self.jobs) + len(self.missing_job_ids) > 100:
            raise ValueError("A Print Job page cannot contain more than 100 identifiers.")
        if not all(isinstance(job, PrintJob) for job in self.jobs):
            raise TypeError("A Print Job page can contain only Print Job snapshots.")
        present = [job.job_id for job in self.jobs]
        if len(set(present)) != len(present):
            raise ValueError("A Print Job page cannot contain duplicate jobs.")
        for job_id in self.missing_job_ids:
            _validate_identifier("missing_job_id", job_id)
        if len(set(self.missing_job_ids)) != len(self.missing_job_ids):
            raise ValueError("A Print Job page cannot repeat a missing identifier.")
        overlap = set(present).intersection(self.missing_job_ids)
        if overlap:
            raise ValueError("A Print Job cannot be both present and missing.")


@dataclass(frozen=True, slots=True)
class EventStreamReady:
    stream_id: str
    current_sequence: int
    high_water_mark: int

    def __post_init__(self) -> None:
        _validate_identifier("stream_id", self.stream_id)
        if type(self.current_sequence) is not int or self.current_sequence < 0:
            raise ValueError("Event current_sequence must be nonnegative.")
        if type(self.high_water_mark) is not int or self.high_water_mark < 0:
            raise ValueError("Event high_water_mark must be nonnegative.")
        if self.high_water_mark > self.current_sequence:
            raise ValueError("Event high_water_mark cannot exceed current_sequence.")


@dataclass(frozen=True, slots=True)
class JobEvent:
    sequence: int
    job: PrintJob
    event_type: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Job event sequence must be positive.")
        if not isinstance(self.job, PrintJob):
            raise TypeError("A Job Event requires a Print Job snapshot.")
        _validate_identifier("event_type", self.event_type)
        object.__setattr__(self, "occurred_at", _utc_required(self.occurred_at))


EventStreamItem: TypeAlias = EventStreamReady | JobEvent


def _validate_pos_origin(origin: PosPrintOrigin | PreparationPrintOrigin) -> None:
    for name in (
        "organization_id",
        "site_id",
        "database",
        "paired_client_id",
        "pos_configuration_id",
        "pos_session_id",
        "offline_order_id",
        "document_kind",
        "content_revision",
    ):
        _validate_identifier(name, getattr(origin, name))
    if origin.server_order_id is not None:
        _validate_identifier("server_order_id", origin.server_order_id)


def _is_pos_origin(value: object) -> bool:
    return type(value) in {PosPrintOrigin, PreparationPrintOrigin}


def _is_print_origin(value: object) -> bool:
    return type(value) in {PosPrintOrigin, PreparationPrintOrigin, ReportPrintOrigin}


def _validate_lifecycle(
    *,
    state: PrintJobState,
    started_at: datetime | None,
    terminal_at: datetime | None,
    error_code: str | None,
    message_key: str | None,
    confirmation_evidence: OutputEvidence | None,
) -> None:
    if state is PrintJobState.ACCEPTED:
        if started_at is not None or terminal_at is not None:
            raise ValueError("An accepted Print Job cannot have lifecycle markers.")
    elif state is PrintJobState.IN_PROGRESS:
        if started_at is None or terminal_at is not None:
            raise ValueError("An in-progress Print Job requires started_at only.")
    elif state is PrintJobState.OUTPUT_CONFIRMED:
        if started_at is None or terminal_at is None:
            raise ValueError(
                "An output_confirmed Print Job requires started_at and terminal_at."
            )
        if confirmation_evidence is None:
            raise ValueError(
                "An output_confirmed Print Job requires confirmation_evidence."
            )
    elif state is PrintJobState.OUTCOME_UNKNOWN:
        if started_at is None or terminal_at is None:
            raise ValueError(
                "An outcome_unknown Print Job requires started_at and terminal_at."
            )
        _validate_failure(error_code, message_key)
    elif state is PrintJobState.FAILED:
        if terminal_at is None:
            raise ValueError("A failed Print Job requires terminal_at.")
        _validate_failure(error_code, message_key)
    else:
        if terminal_at is None:
            raise ValueError(f"A {state.value} Print Job requires terminal_at.")
        if started_at is not None:
            raise ValueError(f"A {state.value} Print Job cannot have started_at.")
    has_error = error_code is not None or message_key is not None
    if state not in {PrintJobState.FAILED, PrintJobState.OUTCOME_UNKNOWN} and has_error:
        raise ValueError(f"A {state.value} Print Job cannot contain an error.")
    if state is not PrintJobState.OUTPUT_CONFIRMED and confirmation_evidence is not None:
        raise ValueError(f"A {state.value} Print Job cannot contain Output Evidence.")


def _validate_first_io_marker(
    job: PrintJob, marker: FirstIoMarker | None, at: datetime
) -> None:
    if marker is None:
        raise ValueError("The in_progress transition requires a durable FirstIoMarker.")
    if marker.job_id != job.job_id:
        raise ValueError("FirstIoMarker does not belong to this Print Job.")
    if marker.committed_at != at:
        raise ValueError("FirstIoMarker commit time must equal the transition time.")


def _validate_failure(error_code: str | None, message_key: str | None) -> None:
    if not isinstance(error_code, str) or not _ERROR_CODE_PATTERN.fullmatch(error_code):
        raise ValueError("error_code must be a safe lower-case contract code.")
    if not isinstance(message_key, str) or not _MESSAGE_KEY_PATTERN.fullmatch(
        message_key
    ):
        raise ValueError("message_key must be a safe lower-case localization key.")


def _validate_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            f"{name} must be a safe identifier of at most {MAX_IDENTIFIER_LENGTH} characters."
        )


def _utc_required(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Print Job timestamps must include a timezone.")
    return value.astimezone(UTC)


def _utc_optional(value: datetime | None) -> datetime | None:
    return None if value is None else _utc_required(value)
