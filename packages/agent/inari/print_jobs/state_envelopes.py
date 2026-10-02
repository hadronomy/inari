from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import re

from .models import OutputEvidence, PayloadFingerprint, PrintJob, PrintJobState


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


@dataclass(frozen=True, slots=True)
class AgentStateObservation:
    envelope_id: str
    agent_id: str
    agent_boot_id: str
    reconciliation_session_id: str
    dispatch_epoch: int
    envelope_sequence: int
    durable_state_sequence: int
    observed_at: datetime
    job: PrintJob
    payload_fingerprint: PayloadFingerprint

    def __post_init__(self) -> None:
        for name in (
            "envelope_id",
            "agent_id",
            "agent_boot_id",
            "reconciliation_session_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a bounded identifier.")
        for name in ("dispatch_epoch", "envelope_sequence", "durable_state_sequence"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 2**53 - 1:
                raise ValueError(f"{name} must be a positive JSON-safe integer.")
        if self.observed_at.tzinfo is None:
            raise ValueError("Agent State observation time requires a time zone.")
        if not isinstance(self.job, PrintJob):
            raise TypeError("Agent State requires an authoritative Print Job.")
        if not isinstance(self.payload_fingerprint, PayloadFingerprint):
            raise TypeError("Agent State requires a Payload Fingerprint.")
        latest_at = self.job.terminal_at or self.job.started_at or self.job.accepted_at
        if self.observed_at < latest_at:
            raise ValueError("Agent State cannot precede its Print Job state.")
        if (
            self.job.state is PrintJobState.OUTPUT_CONFIRMED
            and self.job.confirmation_evidence is not OutputEvidence.DEVICE
        ):
            raise ValueError("Confirmed Agent State requires Device Output Evidence.")

    def claims(self) -> dict[str, object]:
        job = self.job
        return {
            "contract_major": 1,
            "envelope_id": self.envelope_id,
            "agent_id": self.agent_id,
            "agent_boot_id": self.agent_boot_id,
            "reconciliation_session_id": self.reconciliation_session_id,
            "dispatch_epoch": self.dispatch_epoch,
            "envelope_sequence": self.envelope_sequence,
            "durable_state_sequence": self.durable_state_sequence,
            "observed_at": _timestamp(self.observed_at),
            "issued_at": _timestamp(self.observed_at),
            "payload_fingerprint": f"sha256:{self.payload_fingerprint.value.hex()}",
            "job": {
                "print_job_id": job.job_id,
                "print_intent_id": job.intent_id,
                "device_id": job.device_id,
                "origin": {"kind": job.origin.kind.value, **asdict(job.origin)},
                "managed_work_id": job.managed_work_id,
                "state": job.state.value,
                "state_version": job.state_version,
                "accepted_at": _timestamp(job.accepted_at),
                "started_at": _timestamp(job.started_at),
                "terminal_at": _timestamp(job.terminal_at),
                "expires_at": _timestamp(job.expires_at),
                "error_code": job.error_code,
                "message_key": job.message_key,
                "confirmation_evidence": (
                    None
                    if job.confirmation_evidence is None
                    else OutputEvidence(job.confirmation_evidence).value
                ),
                "contract_version": job.contract_version,
            },
        }


def _timestamp(value: datetime | None) -> str | None:
    return (
        None
        if value is None
        else value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
