from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from inari.print_jobs import (
    EventStreamReady,
    FirstIoMarker,
    JobEvent,
    LateEvidenceOutcome,
    OutputEvidence,
    PairedClientScope,
    PayloadFingerprint,
    PosPrintOrigin,
    PreparationPrintOrigin,
    PrintJob,
    PrintJobPage,
    PrintJobQuery,
    PrintIntentPage,
    PrintIntentQuery,
    PrintJobState,
    ReportPrintOrigin,
    SiteManagerScope,
    SubmissionResult,
    annotate_late_evidence,
    can_transition,
    transition,
)


NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


def pos_origin(**changes: object) -> PosPrintOrigin:
    values: dict[str, object] = {
        "organization_id": "org_01",
        "site_id": "site_01",
        "database": "odoo_prod",
        "paired_client_id": "client_01",
        "pos_configuration_id": "pos_01",
        "pos_session_id": "session_01",
        "offline_order_id": "01991a84-d0c2-7a49-89ad-2fd14bdbe501",
        "server_order_id": None,
        "document_kind": "customer_receipt",
        "content_revision": "sha256:receipt_revision",
    }
    values.update(changes)
    return PosPrintOrigin(**values)


def job(**changes: object) -> PrintJob:
    values: dict[str, object] = {
        "job_id": "job_01",
        "intent_id": "intent_01",
        "device_id": "dev_01",
        "origin": pos_origin(),
        "managed_work_id": None,
        "state": PrintJobState.ACCEPTED,
        "state_version": 1,
        "accepted_at": NOW,
        "started_at": None,
        "terminal_at": None,
        "expires_at": NOW + timedelta(minutes=5),
        "retryable": True,
        "error_code": None,
        "message_key": None,
        "confirmation_evidence": None,
        "contract_version": "v1",
    }
    values.update(changes)
    return PrintJob(**values)


def first_io(at: datetime) -> FirstIoMarker:
    return FirstIoMarker(
        marker_id="iom_01", job_id="job_01", committed_at=at, durable_sequence=8
    )


def outcome_unknown(at: datetime = NOW + timedelta(seconds=3)) -> PrintJob:
    started_at = NOW + timedelta(seconds=2)
    started = transition(
        job(),
        PrintJobState.IN_PROGRESS,
        at=started_at,
        first_io_marker=first_io(started_at),
    )
    return transition(
        started,
        PrintJobState.OUTCOME_UNKNOWN,
        at=at,
        error_code="outcome_unknown",
        message_key="print.outcome_unknown",
    )


def test_public_state_machine_is_strict() -> None:
    assert can_transition(PrintJobState.ACCEPTED, PrintJobState.IN_PROGRESS)
    assert can_transition(PrintJobState.IN_PROGRESS, PrintJobState.OUTCOME_UNKNOWN)
    assert can_transition(PrintJobState.OUTCOME_UNKNOWN, PrintJobState.OUTPUT_CONFIRMED)
    assert not can_transition(PrintJobState.ACCEPTED, PrintJobState.OUTPUT_CONFIRMED)
    assert not can_transition(PrintJobState.IN_PROGRESS, PrintJobState.CANCELED)
    assert not can_transition(PrintJobState.FAILED, PrintJobState.IN_PROGRESS)


def test_in_progress_requires_matching_durable_first_io_marker() -> None:
    at = NOW + timedelta(seconds=2)
    with pytest.raises(ValueError, match="FirstIoMarker"):
        transition(job(), PrintJobState.IN_PROGRESS, at=at)
    with pytest.raises(ValueError, match="does not belong"):
        transition(
            job(),
            PrintJobState.IN_PROGRESS,
            at=at,
            first_io_marker=replace(first_io(at), job_id="job_02"),
        )
    with pytest.raises(ValueError, match="commit time"):
        transition(
            job(),
            PrintJobState.IN_PROGRESS,
            at=at,
            first_io_marker=first_io(at + timedelta(seconds=1)),
        )


def test_transition_sets_monotonic_lifecycle_fields() -> None:
    started_at = NOW + timedelta(seconds=2)
    started = transition(
        job(),
        PrintJobState.IN_PROGRESS,
        at=started_at,
        first_io_marker=first_io(started_at),
    )
    assert started.state_version == 2
    assert started.started_at == started_at
    confirmed = transition(
        started,
        PrintJobState.OUTPUT_CONFIRMED,
        at=NOW + timedelta(seconds=3),
        confirmation_evidence=OutputEvidence.DEVICE,
    )
    assert confirmed.state_version == 3
    assert confirmed.terminal_at == NOW + timedelta(seconds=3)
    assert confirmed.confirmation_evidence is OutputEvidence.DEVICE


def test_start_and_expiry_respect_the_accepted_deadline() -> None:
    deadline = NOW + timedelta(minutes=5)
    with pytest.raises(ValueError, match="after expires_at"):
        transition(
            job(),
            PrintJobState.IN_PROGRESS,
            at=deadline + timedelta(microseconds=1),
            first_io_marker=first_io(deadline + timedelta(microseconds=1)),
        )
    with pytest.raises(ValueError, match="before expires_at"):
        transition(job(), PrintJobState.EXPIRED, at=deadline - timedelta(seconds=1))
    assert (
        transition(job(), PrintJobState.EXPIRED, at=deadline).state
        is PrintJobState.EXPIRED
    )


def test_outcome_unknown_can_improve_only_during_active_reconciliation() -> None:
    unknown = outcome_unknown()
    assert unknown.terminal_at is not None
    confirmed = transition(
        unknown,
        PrintJobState.OUTPUT_CONFIRMED,
        at=unknown.terminal_at + timedelta(hours=24),
        confirmation_evidence=OutputEvidence.SPOOLER,
    )
    assert confirmed.state is PrintJobState.OUTPUT_CONFIRMED
    with pytest.raises(ValueError, match="24-hour"):
        transition(
            unknown,
            PrintJobState.FAILED,
            at=unknown.terminal_at + timedelta(hours=24, microseconds=1),
            error_code="device_unavailable",
            message_key="print.device_unavailable",
        )


def test_late_evidence_is_an_annotation_and_does_not_rewrite_unknown_state() -> None:
    unknown = outcome_unknown()
    assert unknown.terminal_at is not None
    annotation = annotate_late_evidence(
        unknown,
        outcome=LateEvidenceOutcome.CONFIRMED,
        observed_at=unknown.terminal_at + timedelta(hours=25),
        confirmation_evidence=OutputEvidence.DEVICE,
    )
    assert annotation.job_id == unknown.job_id
    assert annotation.observed_state_version == unknown.state_version
    assert annotation.outcome is LateEvidenceOutcome.CONFIRMED
    assert unknown.state is PrintJobState.OUTCOME_UNKNOWN
    with pytest.raises(ValueError, match="active reconciliation"):
        annotate_late_evidence(
            unknown,
            outcome=LateEvidenceOutcome.FAILED,
            observed_at=unknown.terminal_at + timedelta(hours=1),
            error_code="device_unavailable",
            message_key="print.device_unavailable",
        )


def test_public_snapshot_enforces_lifecycle_and_safe_errors() -> None:
    with pytest.raises(ValueError, match="started_at"):
        job(state=PrintJobState.IN_PROGRESS)
    with pytest.raises(ValueError, match="confirmation_evidence"):
        job(
            state=PrintJobState.OUTPUT_CONFIRMED,
            started_at=NOW,
            terminal_at=NOW,
        )
    with pytest.raises(ValueError, match="message_key"):
        job(
            state=PrintJobState.FAILED,
            terminal_at=NOW,
            error_code="device_unavailable",
        )
    with pytest.raises(ValueError, match="safe lower-case"):
        job(
            state=PrintJobState.FAILED,
            terminal_at=NOW,
            error_code="Driver said: paper out!",
            message_key="print.device_unavailable",
        )


def test_origin_is_a_closed_content_free_union() -> None:
    preparation = PreparationPrintOrigin(
        organization_id="org_01",
        site_id="site_01",
        database="odoo_prod",
        paired_client_id="client_01",
        pos_configuration_id="pos_01",
        pos_session_id="session_01",
        offline_order_id="order_01",
        server_order_id="101",
        document_kind="preparation_receipt",
        content_revision="sha256:content",
        segment_kind="course",
        segment_index=0,
        preparation_revision="sha256:preparation",
    )
    report = ReportPrintOrigin(
        organization_id="org_01",
        site_id="site_01",
        database="odoo_prod",
        company_id="company_01",
        report_binding_id="binding_01",
        report_route="stock_label",
        report_action="action_report_delivery",
        source_model="stock.picking",
        record_ids=("51", "52"),
        wizard_input_digest=None,
        rendered_document_index=0,
    )
    assert preparation.segment_index == 0
    assert report.record_ids == ("51", "52")
    assert not hasattr(job(), "content")
    with pytest.raises(ValueError, match="exactly one"):
        replace(report, wizard_input_digest="sha256:" + "a" * 64)


def test_scope_is_closed_and_fails_closed() -> None:
    client_scope = PairedClientScope(
        organization_id="org_01",
        site_id="site_01",
        pos_configuration_id="pos_01",
        paired_client_id="client_01",
    )
    assert client_scope.allows(job())
    assert not client_scope.allows(job(origin=pos_origin(paired_client_id="client_02")))
    manager_scope = SiteManagerScope(organization_id="org_01", site_id="site_01")
    assert manager_scope.allows(job())
    assert not manager_scope.allows(job(origin=pos_origin(site_id="site_02")))
    assert not client_scope.allows(object())
    assert not manager_scope.allows(object())


def test_payload_fingerprint_is_exact_sha256_length() -> None:
    assert PayloadFingerprint(b"a" * 32).value == b"a" * 32
    with pytest.raises(ValueError, match="32 bytes"):
        PayloadFingerprint(b"short")


def test_batch_query_rejects_empty_invalid_and_oversized_input() -> None:
    scope = SiteManagerScope(organization_id="org_01", site_id="site_01")
    query = PrintJobQuery.from_ids(["job_01", "job_01", "job_02"], scope=scope)
    assert query.job_ids == ("job_01", "job_02")
    for invalid in ([], [""], ["contains space"]):
        with pytest.raises(ValueError):
            PrintJobQuery.from_ids(invalid, scope=scope)
    with pytest.raises(ValueError, match="100"):
        PrintJobQuery.from_ids([f"job_{index}" for index in range(101)], scope=scope)
    with pytest.raises(ValueError, match="identifier"):
        PrintJobQuery(("",), scope=scope)


def test_page_tracks_missing_ids_and_agent_high_water() -> None:
    page = PrintJobPage(jobs=(job(),), missing_job_ids=("job_02",), high_water_mark=7)
    assert page.missing_job_ids == ("job_02",)
    with pytest.raises(ValueError, match="nonnegative"):
        replace(page, high_water_mark=-1)
    with pytest.raises(ValueError, match="both present and missing"):
        replace(page, missing_job_ids=("job_01",))
    with pytest.raises(ValueError, match="100"):
        PrintJobPage(
            jobs=tuple(replace(job(), job_id=f"job_{index}") for index in range(100)),
            missing_job_ids=("job_101",),
            high_water_mark=7,
        )


def test_print_intent_query_and_page_are_bounded_and_exact() -> None:
    scope = PairedClientScope(
        organization_id="org_01",
        site_id="site_01",
        pos_configuration_id="pos_01",
        paired_client_id="client_01",
    )
    query = PrintIntentQuery.from_ids(
        ["intent_01", "intent_01", "intent_02"], scope=scope
    )
    assert query.print_intent_ids == ("intent_01", "intent_02")
    page = PrintIntentPage(
        jobs=(job(),),
        missing_print_intent_ids=("intent_02",),
        high_water_mark=9,
    )
    assert page.high_water_mark == 9
    with pytest.raises(ValueError, match="both present and missing"):
        replace(page, missing_print_intent_ids=("intent_01",))
    with pytest.raises(ValueError, match="100"):
        PrintIntentQuery.from_ids(
            [f"intent_{index}" for index in range(101)], scope=scope
        )


def test_event_stream_starts_with_ready_reconciliation_barrier() -> None:
    ready = EventStreamReady(
        stream_id="stream_01", current_sequence=12, high_water_mark=10
    )
    event = JobEvent(
        sequence=13, job=job(), event_type="state_changed", occurred_at=NOW
    )
    assert ready.high_water_mark <= ready.current_sequence < event.sequence
    with pytest.raises(ValueError, match="high_water_mark"):
        replace(ready, current_sequence=9)


def test_submission_result_is_an_immutable_snapshot() -> None:
    result = SubmissionResult(job=job(), replayed=True)
    assert result.replayed
