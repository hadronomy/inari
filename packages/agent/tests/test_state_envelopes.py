from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from inari.print_jobs import (
    OutputEvidence,
    PayloadFingerprint,
    PrintJob,
    PrintJobState,
    ReportPrintOrigin,
)
from inari.print_jobs.state_envelopes import AgentStateObservation


NOW = datetime(2026, 9, 6, tzinfo=UTC)


@pytest.fixture
def observation():
    return AgentStateObservation(
        envelope_id="state-1",
        agent_id="agent-1",
        agent_boot_id="boot-1",
        reconciliation_session_id="recon-1",
        dispatch_epoch=1,
        envelope_sequence=1,
        durable_state_sequence=1,
        observed_at=NOW,
        payload_fingerprint=PayloadFingerprint(b"f" * 32),
        job=PrintJob(
            job_id="job-1",
            intent_id="intent-1",
            device_id="device-1",
            origin=ReportPrintOrigin(
                organization_id="org-1",
                site_id="site-1",
                database="odoo",
                company_id="7",
                report_binding_id="binding-1",
                report_route="manual",
                report_action="stock.action_report_delivery",
                source_model="stock.picking",
                record_ids=("17",),
                wizard_input_digest=None,
                rendered_document_index=0,
            ),
            state=PrintJobState.ACCEPTED,
            state_version=1,
            accepted_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            retryable=False,
            contract_version="v1",
            managed_work_id="work-1",
        ),
    )


@pytest.mark.parametrize(
    "field", ["dispatch_epoch", "envelope_sequence", "durable_state_sequence"]
)
@pytest.mark.parametrize("value", [True, 0, -1, 2**53])
def test_observation_requires_positive_json_safe_counters(observation, field, value):
    with pytest.raises(ValueError, match="positive JSON-safe integer"):
        replace(observation, **{field: value})


def test_observation_cannot_precede_durable_job_state(observation):
    with pytest.raises(ValueError, match="cannot precede"):
        replace(observation, observed_at=NOW - timedelta(seconds=1))


@pytest.mark.parametrize("evidence", [OutputEvidence.SPOOLER, OutputEvidence.TRANSPORT])
def test_confirmed_observation_requires_device_evidence(observation, evidence):
    job = replace(
        observation.job,
        state=PrintJobState.OUTPUT_CONFIRMED,
        state_version=3,
        started_at=NOW,
        terminal_at=NOW,
        confirmation_evidence=evidence,
    )
    with pytest.raises(ValueError, match="requires Device Output Evidence"):
        replace(observation, job=job)


def test_confirmed_observation_keeps_device_evidence(observation):
    job = replace(
        observation.job,
        state=PrintJobState.OUTPUT_CONFIRMED,
        state_version=3,
        started_at=NOW,
        terminal_at=NOW,
        confirmation_evidence=OutputEvidence.DEVICE,
    )
    claims = replace(observation, job=job).claims()
    assert claims["job"]["confirmation_evidence"] == "device"
    assert claims["job"]["state"] == "output_confirmed"
