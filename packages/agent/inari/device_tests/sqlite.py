from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from uuid import uuid4

from sqlalchemy import case, insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..client_trust import AuthorizedRequest, Permission
from ..core.failures import DomainFailure, ProblemCode
from ..db.schema import (
    client_grants_table,
    client_pairings_table,
    device_tests_table as tests,
    device_test_evidence_table,
    spool_reservations_table,
)
from ..device_authority import canonical_json_bytes
from ..device_authority.authority import binding_revision_payload, device_test_payload
from ..device_authority.models import SignedDeviceTestEvidence
from ..device_authority.testing import DeviceTestAuthorization
from ..physical_execution.models import DriverExecutionResult, DriverOutcome
from ..runtime.store import RuntimeStore
from .models import DeviceTestRecord, DeviceTestRequest, TestIoMarker, TestState


class SqliteDeviceTestLedger:
    """Commit the Device Test I/O marker before granting the worker permission."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def find(
        self, test_id: str, authorization: AuthorizedRequest, *, now: datetime
    ) -> DeviceTestRecord | None:
        with self.store.immediate_transaction() as connection:
            self._expire(connection, now)
            self._check_grant(connection, authorization, now)
            row = (
                connection.execute(
                    select(tests).where(
                        tests.c.test_id == test_id, *_owned_scope(authorization)
                    )
                )
                .mappings()
                .first()
            )
            return _record(row) if row is not None else None

    def admit(
        self,
        request: DeviceTestRequest,
        authorization: AuthorizedRequest,
        fingerprint: bytes,
        *,
        now: datetime,
        deadline: datetime,
    ) -> tuple[DeviceTestRecord, bool]:
        with self.store.immediate_transaction() as connection:
            self._expire(connection, now)
            self._check_grant(connection, authorization, now)
            existing = (
                connection.execute(
                    select(tests).where(
                        tests.c.test_id == request.test_id, *_base_scope(authorization)
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if (
                    existing["paired_client_id"] != authorization.grant.pairing_id
                    or existing["actor_id"] != authorization.grant.actor_id
                ):
                    raise DomainFailure(ProblemCode.PERMISSION_DENIED)
                if bytes(existing["fingerprint"]) != fingerprint:
                    raise DomainFailure(ProblemCode.IDEMPOTENCY_CONFLICT)
                return _record(existing), False
            if self._busy(connection, request.device_id):
                raise DomainFailure(ProblemCode.DEVICE_UNAVAILABLE)
            values = {
                **scope_values(authorization),
                "record_id": uuid4().hex,
                "test_id": request.test_id,
                "device_id": request.device_id,
                "binding_revision_id": request.binding_revision_id,
                "fingerprint": fingerprint,
                "state": TestState.ACCEPTED.value,
                "state_version": 1,
                "accepted_at": timestamp(now),
                "io_deadline": timestamp(deadline),
            }
            connection.execute(insert(tests).values(**values))
            row = (
                connection.execute(
                    select(tests).where(tests.c.record_id == values["record_id"])
                )
                .mappings()
                .one()
            )
            return _record(row), True

    def mark_io_started(
        self,
        record: DeviceTestRecord,
        authorization: AuthorizedRequest,
        check: Callable[[], DeviceTestAuthorization],
        *,
        now: datetime,
    ) -> TestIoMarker | None:
        with self.store.immediate_transaction() as connection:
            self._expire(connection, now)
            current = (
                connection.execute(
                    select(tests).where(
                        tests.c.record_id == record.record_id,
                        *_owned_scope(authorization),
                    )
                )
                .mappings()
                .one()
            )
            if current["state"] != TestState.ACCEPTED.value:
                return None
            self._check_grant(connection, authorization, now)
            # The writer lock keeps installation and revocation out of the gap
            # between the local authority check and the durable I/O marker.
            authority = check()
            graph = {
                "binding": binding_revision_payload(authority.binding.revision),
                "binding_digest": authority.binding.digest,
                "profile_digest": authority.profile.digest,
                "profile_id": authority.profile.profile.profile_id,
                "matrix_digest": authority.certification.digest,
                "authority_revision_id": authority.revision.revision.revision_id,
                "authority_revision_digest": authority.revision.digest,
                "observation_digest": authority.observation.digest,
                "required_output_evidence": authority.capability.output_evidence.value,
                "valid_until": _valid_until(authority),
            }
            marker = TestIoMarker(record.record_id, record.device_id, uuid4().hex)
            connection.execute(
                update(tests)
                .where(tests.c.record_id == record.record_id)
                .values(
                    state=TestState.IN_PROGRESS.value,
                    state_version=tests.c.state_version + 1,
                    marker_id=marker.marker_id,
                    started_at=timestamp(now),
                    graph=_json(graph),
                )
            )
        return marker

    def mark_worker_stop_failed(self, record_id: str) -> None:
        with self.store.immediate_transaction() as connection:
            connection.execute(
                update(tests)
                .where(
                    tests.c.record_id == record_id,
                    tests.c.state.in_(
                        (TestState.ACCEPTED.value, TestState.IN_PROGRESS.value)
                    ),
                )
                .values(
                    state_version=tests.c.state_version + 1,
                    error_code="worker_stop_failed",
                )
            )

    def finish_io(
        self, record_id: str, result: DriverExecutionResult, *, now: datetime
    ) -> None:
        state = (
            TestState.AWAITING_CHECKS
            if result.outcome is DriverOutcome.CONFIRMED
            else TestState.OUTCOME_UNKNOWN
        )
        with self.store.immediate_transaction() as connection:
            connection.execute(
                update(tests)
                .where(
                    tests.c.record_id == record_id,
                    tests.c.state == TestState.IN_PROGRESS.value,
                )
                .values(
                    state=state.value,
                    state_version=tests.c.state_version + 1,
                    output_evidence=result.evidence.value if result.evidence else None,
                    platform_job_id=result.platform_job_id,
                    error_code=result.error_code,
                )
            )

    def fail_before_io(
        self, record_id: str, *, now: datetime, error_code: str = "device_unavailable"
    ) -> None:
        with self.store.immediate_transaction() as connection:
            connection.execute(
                update(tests)
                .where(
                    tests.c.record_id == record_id,
                    tests.c.state == TestState.ACCEPTED.value,
                )
                .values(
                    state=TestState.FAILED_ENVIRONMENT.value,
                    state_version=tests.c.state_version + 1,
                    error_code=error_code,
                    terminal_at=timestamp(now),
                )
            )

    def finalize(
        self,
        test_id: str,
        authorization: AuthorizedRequest,
        checks: dict[str, str],
        sign: Callable[
            [DeviceTestRecord],
            tuple[dict[str, object], SignedDeviceTestEvidence | None],
        ],
        *,
        now: datetime,
    ) -> DeviceTestRecord:
        with self.store.immediate_transaction() as connection:
            self._expire(connection, now)
            self._check_grant(connection, authorization, now)
            row = (
                connection.execute(
                    select(tests).where(
                        tests.c.test_id == test_id, *_owned_scope(authorization)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainFailure(ProblemCode.RESOURCE_NOT_FOUND)
            record = _record(row)
            if record.state is TestState.COMPLETED:
                if record.checks != checks:
                    raise DomainFailure(ProblemCode.IDEMPOTENCY_CONFLICT)
                return record
            if record.state in {TestState.ACCEPTED, TestState.IN_PROGRESS}:
                raise DomainFailure(ProblemCode.DEVICE_UNAVAILABLE)
            signed_result, evidence = sign(record)
            if evidence is not None:
                assert record.graph is not None
                value = device_test_payload(evidence.evidence)
                for name in (
                    "device_identity_digest",
                    "driver_profile_digest",
                    "test_pattern_digest",
                ):
                    value[name] = bytes.fromhex(str(value[name]))
                connection.execute(
                    insert(device_test_evidence_table).values(
                        **value,
                        evidence_digest=bytes.fromhex(evidence.digest),
                        signer_key_id=evidence.signer_key_id,
                        signature=evidence.signature,
                        authority_revision_id=str(
                            record.graph["authority_revision_id"]
                        ),
                    )
                )
            connection.execute(
                update(tests)
                .where(tests.c.record_id == record.record_id)
                .values(
                    state=TestState.COMPLETED.value,
                    state_version=tests.c.state_version + 1,
                    checks=_json(checks),
                    signed_result=_json(signed_result),
                    terminal_at=timestamp(now),
                )
            )
            result_row = (
                connection.execute(
                    select(tests).where(tests.c.record_id == record.record_id)
                )
                .mappings()
                .one()
            )
            return _record(result_row)

    @staticmethod
    def _expire(connection: Connection, now: datetime) -> None:
        for prior, state in ((TestState.ACCEPTED, TestState.FAILED_ENVIRONMENT),):
            connection.execute(
                update(tests)
                .where(
                    tests.c.state == prior.value, tests.c.io_deadline <= timestamp(now)
                )
                .values(
                    state=state.value,
                    state_version=tests.c.state_version + 1,
                    error_code="execution_deadline",
                    terminal_at=timestamp(now),
                )
            )

    def recover_after_restart(self, *, now: datetime) -> None:
        """Release abandoned reservations without repeating Device I/O."""
        with self.store.immediate_transaction() as connection:
            for prior, state in (
                (TestState.ACCEPTED, TestState.FAILED_ENVIRONMENT),
                (TestState.IN_PROGRESS, TestState.OUTCOME_UNKNOWN),
            ):
                connection.execute(
                    update(tests)
                    .where(tests.c.state == prior.value)
                    .values(
                        state=state.value,
                        state_version=tests.c.state_version + 1,
                        error_code=case(
                            (
                                tests.c.error_code == "worker_stop_failed",
                                tests.c.error_code,
                            ),
                            else_="agent_restarted",
                        ),
                        terminal_at=timestamp(now),
                    )
                )

    @staticmethod
    def _busy(connection: Connection, device_id: str) -> bool:
        business = connection.execute(
            select(spool_reservations_table.c.id).where(
                spool_reservations_table.c.device_id == device_id,
                spool_reservations_table.c.reservation_kind == "execution_temp",
                spool_reservations_table.c.state == "held",
            )
        ).first()
        test = connection.execute(
            select(tests.c.record_id).where(
                tests.c.device_id == device_id,
                tests.c.state.in_(
                    (TestState.ACCEPTED.value, TestState.IN_PROGRESS.value)
                ),
            )
        ).first()
        return business is not None or test is not None

    @staticmethod
    def _check_grant(
        connection: Connection, authorization: AuthorizedRequest, now: datetime
    ) -> None:
        supplied = authorization.grant
        grant = (
            connection.execute(
                select(client_grants_table).where(
                    client_grants_table.c.grant_id == supplied.grant_id
                )
            )
            .mappings()
            .first()
        )
        pairing = (
            connection.execute(
                select(client_pairings_table).where(
                    client_pairings_table.c.pairing_id == supplied.pairing_id
                )
            )
            .mappings()
            .first()
        )
        expected = scope_values(authorization)
        if (
            grant is None
            or pairing is None
            or any(
                grant[name] != value
                for name, value in expected.items()
                if name != "paired_client_id"
            )
            or (
                grant["pairing_id"] != supplied.pairing_id
                or grant["generation"] != supplied.generation
                or grant["authorization_digest"] != supplied.authorization_digest
                or grant["lifecycle"] != "active"
                or pairing["lifecycle"] != "active"
                or parse_time(grant["expires_at"]) <= now
                or (
                    pairing["expires_at"] is not None
                    and parse_time(pairing["expires_at"]) <= now
                )
                or Permission.DEVICE_TEST.value not in json.loads(grant["permissions"])
                or grant["role"] != "device_manager"
            )
        ):
            raise DomainFailure(ProblemCode.PERMISSION_DENIED)


def scope_values(authorization: AuthorizedRequest) -> dict[str, str]:
    business = authorization.grant.scope.business
    if business.pos_configuration_id is None:
        raise DomainFailure(ProblemCode.BINDING_REQUIRED)
    return {
        "database": business.database,
        "company_id": business.company_id,
        "organization_id": business.organization_id,
        "site_id": business.site_id,
        "pos_configuration_id": business.pos_configuration_id,
        "paired_client_id": authorization.grant.pairing_id,
        "actor_id": authorization.grant.actor_id,
    }


def _base_scope(authorization: AuthorizedRequest):
    return tuple(
        tests.c[name] == value
        for name, value in scope_values(authorization).items()
        if name not in {"paired_client_id", "actor_id"}
    )


def _owned_scope(authorization: AuthorizedRequest):
    return tuple(
        tests.c[name] == value for name, value in scope_values(authorization).items()
    )


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("The Device Test clock must include a timezone.")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _json(value: Mapping[str, object]) -> str:
    return canonical_json_bytes(value).decode()


def _valid_until(authority: DeviceTestAuthorization) -> str | None:
    expiries = tuple(
        value
        for value in (
            authority.revision.revision.expires_at,
            authority.profile.profile.expires_at,
            authority.certification.row.expires_at,
        )
        if value is not None
    )
    return timestamp(min(expiries)) if expiries else None


def _record(row: RowMapping) -> DeviceTestRecord:
    return DeviceTestRecord(
        record_id=row["record_id"],
        test_id=row["test_id"],
        device_id=row["device_id"],
        binding_revision_id=row["binding_revision_id"],
        fingerprint=bytes(row["fingerprint"]),
        state=TestState(row["state"]),
        state_version=row["state_version"],
        accepted_at=parse_time(row["accepted_at"]),
        io_deadline=parse_time(row["io_deadline"]),
        started_at=parse_time(row["started_at"]) if row["started_at"] else None,
        terminal_at=parse_time(row["terminal_at"]) if row["terminal_at"] else None,
        output_evidence=row["output_evidence"],
        platform_job_id=row["platform_job_id"],
        error_code=row["error_code"],
        graph=json.loads(row["graph"]) if row["graph"] else None,
        checks=json.loads(row["checks"]) if row["checks"] else None,
        signed_result=json.loads(row["signed_result"])
        if row["signed_result"]
        else None,
    )
