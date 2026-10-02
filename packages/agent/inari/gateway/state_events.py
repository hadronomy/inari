from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from uuid import uuid4

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping

from ..db.schema import (
    device_work_admissions_table,
    gateway_inbound_commands_table,
    gateway_outbox_table,
    gateway_print_job_cursors_table,
    public_print_job_events_table,
    public_print_jobs_table,
)
from ..print_jobs import PayloadFingerprint, PrintJob
from ..print_jobs.sqlite import print_job_from_row
from ..print_jobs.state_envelopes import AgentStateObservation
from ..runtime.store import RuntimeStore, dump_json
from ..security.state_keys import AgentStateSigningKeyService
from .models import AgentManagedScope
from .protocol import AgentRuntimeEventMessage, GatewayRuntimeEventPayload
from .repositories import recipient_scope_key


class GatewayStateEventProjector:
    """Commit signed Print Job observations and their journal cursor together."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        signing_keys: AgentStateSigningKeyService,
        agent_boot_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._signing_keys = signing_keys
        self._agent_boot_id = agent_boot_id
        self._clock = clock

    def project(
        self, *, scope: AgentManagedScope, dispatch_epoch: int, limit: int = 128
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise ValueError(
                "The Agent State projection batch must contain 1 to 1024 events."
            )
        scope_key = recipient_scope_key(scope)
        observed_at = self._clock()
        session_id = f"recon_{uuid4().hex}"
        projected = 0
        with self._store.immediate_transaction() as connection:
            last_sequence = (
                connection.execute(
                    select(gateway_print_job_cursors_table.c.last_sequence).where(
                        gateway_print_job_cursors_table.c.recipient_scope == scope_key
                    )
                ).scalar_one_or_none()
                or 0
            )
            rows = (
                connection.execute(
                    select(
                        public_print_jobs_table,
                        device_work_admissions_table.c.fingerprint,
                        public_print_job_events_table.c.sequence.label(
                            "event_sequence"
                        ),
                        public_print_job_events_table.c.state_version.label(
                            "event_state_version"
                        ),
                        public_print_job_events_table.c.snapshot_json,
                        public_print_job_events_table.c.occurred_at,
                    )
                    .select_from(
                        public_print_job_events_table.join(
                            public_print_jobs_table,
                            public_print_jobs_table.c.id
                            == public_print_job_events_table.c.job_id,
                        ).join(
                            device_work_admissions_table,
                            device_work_admissions_table.c.id
                            == public_print_jobs_table.c.admission_id,
                        )
                    )
                    .where(public_print_job_events_table.c.sequence > last_sequence)
                    .order_by(public_print_job_events_table.c.sequence)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            for row in rows:
                last_sequence = row["event_sequence"]
                if (
                    row["managed_work_id"] is None
                    or row["organization_id"] != scope.organization_id
                    or row["site_id"] != scope.site_id
                ):
                    continue
                command_id = connection.execute(
                    select(gateway_inbound_commands_table.c.command_id)
                    .where(
                        gateway_inbound_commands_table.c.message_type
                        == "controller.command.dispatch_device_work",
                        func.json_extract(
                            gateway_inbound_commands_table.c.payload_json,
                            "$.payload.managed_work_id",
                        )
                        == row["managed_work_id"],
                        func.json_extract(
                            gateway_inbound_commands_table.c.payload_json,
                            "$.payload.authenticated_data.agent_id",
                        )
                        == scope.agent_id,
                        func.json_extract(
                            gateway_inbound_commands_table.c.payload_json,
                            "$.payload.authenticated_data.organization_id",
                        )
                        == scope.organization_id,
                        func.json_extract(
                            gateway_inbound_commands_table.c.payload_json,
                            "$.payload.authenticated_data.site_id",
                        )
                        == scope.site_id,
                    )
                    .order_by(
                        gateway_inbound_commands_table.c.received_at,
                        gateway_inbound_commands_table.c.command_id,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if command_id is None:
                    continue
                job = _event_job(row)
                observation = AgentStateObservation(
                    envelope_id=f"ase_{uuid4().hex}",
                    agent_id=scope.agent_id,
                    agent_boot_id=self._agent_boot_id,
                    reconciliation_session_id=session_id,
                    dispatch_epoch=dispatch_epoch,
                    envelope_sequence=last_sequence,
                    durable_state_sequence=last_sequence,
                    observed_at=observed_at,
                    job=job,
                    payload_fingerprint=PayloadFingerprint(row["fingerprint"]),
                )
                signed_envelope = self._signing_keys.sign(observation.claims())
                if len(signed_envelope) > 65536:
                    raise ValueError("The Agent State Envelope exceeds its size limit.")
                publication = AgentRuntimeEventMessage(
                    message_id=observation.envelope_id,
                    occurred_at=observed_at,
                    command_id=command_id,
                    job_id=job.job_id,
                    event=GatewayRuntimeEventPayload(
                        sequence=last_sequence,
                        resource_kind="print_job",
                        resource_id=job.job_id,
                        event_type=f"print_job.{job.state.value}",
                        occurred_at=datetime.fromisoformat(row["occurred_at"]),
                        payload={"state_envelope": signed_envelope},
                    ),
                )
                timestamp = observed_at.isoformat()
                connection.execute(
                    insert(gateway_outbox_table).values(
                        message_id=publication.message_id,
                        message_type=publication.type,
                        state="pending",
                        payload_json=dump_json(publication.model_dump(mode="json")),
                        correlation_id=command_id,
                        dedupe_key=f"agent-state:{publication.message_id}",
                        recipient_scope=scope_key,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                projected += 1
            connection.execute(
                sqlite_insert(gateway_print_job_cursors_table)
                .values(
                    recipient_scope=scope_key,
                    last_sequence=last_sequence,
                )
                .on_conflict_do_update(
                    index_elements=[gateway_print_job_cursors_table.c.recipient_scope],
                    set_={"last_sequence": last_sequence},
                )
            )
        return projected


def _event_job(row: RowMapping) -> PrintJob:
    snapshot = json.loads(row["snapshot_json"])
    if not isinstance(snapshot, dict) or any(
        snapshot.get(snapshot_key) != row[column]
        for snapshot_key, column in (
            ("job_id", "id"),
            ("intent_id", "intent_id"),
            ("device_id", "device_id"),
            ("state_version", "event_state_version"),
        )
    ):
        raise RuntimeError("The Print Job event does not match its durable identity.")
    if row["event_state_version"] > row["state_version"]:
        raise RuntimeError("The Print Job event exceeds its current state version.")
    for name in ("accepted_at", "contract_version"):
        if name in snapshot and snapshot[name] != row[name]:
            raise RuntimeError("The Print Job event changed its immutable metadata.")
    values = dict(row)
    for name in (
        "state",
        "state_version",
        "started_at",
        "terminal_at",
        "error_code",
        "message_key",
        "confirmation_evidence",
    ):
        values[name] = snapshot.get(name)
    return print_job_from_row(values)
