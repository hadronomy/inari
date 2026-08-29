from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from sqlalchemy import Table, select
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..core.failures import ProblemCode
from ..db.schema import (
    device_authority_revisions_table,
    device_authority_revocations_table,
    device_authority_signer_keys_table,
    device_authority_state_table,
    device_binding_authority_state_table,
    device_binding_revisions_table,
    device_driver_profiles_table,
    device_test_evidence_table,
    device_work_authority_proof_subjects_table,
    device_work_authority_proofs_table,
    hardware_certification_matrix_rows_table,
)
from ..device_authority import AuthorityProof, RevocationSubjectKind, canonical_digest
from .errors import SpoolAdmissionError
from .manifest import parse_timestamp
from .proofs import read_authority_proof


class ActiveAuthorityGuard(Protocol):
    """Check durable authority through the transaction that creates Accepted."""

    def check(
        self,
        connection: Connection,
        proof: AuthorityProof,
        *,
        now: datetime,
    ) -> None: ...


class SqlActiveAuthorityGuard:
    """Recheck the durable Device Capability graph at the publication barrier."""

    def check(
        self,
        connection: Connection,
        proof: AuthorityProof,
        *,
        now: datetime,
    ) -> None:
        if not connection.in_transaction():
            raise RuntimeError("the authority guard requires an active transaction")
        if not isinstance(proof, AuthorityProof):
            raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)
        if now < proof.issued_at or now >= proof.valid_until:
            raise SpoolAdmissionError(ProblemCode.EXPIRED)

        proof_row = _one(
            connection,
            device_work_authority_proofs_table,
            device_work_authority_proofs_table.c.proof_id == proof.proof_id,
            ProblemCode.CAPABILITY_CHANGED,
        )
        stored = read_authority_proof(
            connection,
            admission_id=str(proof_row["admission_id"]),
        )
        if stored != proof:
            raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)
        _check_subjects(connection, proof)

        state = _one(
            connection,
            device_authority_state_table,
            device_authority_state_table.c.singleton_id == 1,
            ProblemCode.SERVICE_UNAVAILABLE,
        )
        if state["status"] != "ready":
            raise SpoolAdmissionError(ProblemCode.SERVICE_UNAVAILABLE)
        current_revision = _one(
            connection,
            device_authority_revisions_table,
            device_authority_revisions_table.c.revision_id
            == state["current_revision_id"],
            ProblemCode.SERVICE_UNAVAILABLE,
        )
        current_number = _integer(
            current_revision["revision_number"],
            ProblemCode.SERVICE_UNAVAILABLE,
        )
        if current_number < proof.authority_revision_number:
            raise SpoolAdmissionError(ProblemCode.SERVICE_UNAVAILABLE)
        if current_number == proof.authority_revision_number and (
            current_revision["revision_id"] != proof.authority_revision_id
            or _digest(current_revision["revision_digest"])
            != proof.authority_revision_digest
        ):
            raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)
        _check_window(current_revision, now, ProblemCode.SERVICE_UNAVAILABLE)
        _check_signer(
            connection,
            current_revision,
            purpose="authority_revision",
            now=now,
            code=ProblemCode.SERVICE_UNAVAILABLE,
        )
        _check_revocation(
            connection,
            kind=RevocationSubjectKind.AUTHORITY_REVISION.value,
            subject_id=str(current_revision["revision_id"]),
            digest=_digest(current_revision["revision_digest"]),
            code=ProblemCode.SERVICE_UNAVAILABLE,
        )

        admitted_revision = _one(
            connection,
            device_authority_revisions_table,
            device_authority_revisions_table.c.revision_id
            == proof.authority_revision_id,
            ProblemCode.CAPABILITY_CHANGED,
        )
        _require_values(
            admitted_revision,
            {
                "revision_number": proof.authority_revision_number,
                "revision_digest": _bytes(proof.authority_revision_digest),
            },
            ProblemCode.CAPABILITY_CHANGED,
        )
        _check_window(admitted_revision, now, ProblemCode.CAPABILITY_CHANGED)
        _check_signer(
            connection,
            admitted_revision,
            purpose="authority_revision",
            now=now,
            code=ProblemCode.CAPABILITY_CHANGED,
        )

        binding = _one(
            connection,
            device_binding_revisions_table,
            device_binding_revisions_table.c.revision_id
            == proof.binding_revision_id,
            ProblemCode.CAPABILITY_CHANGED,
        )
        _require_values(
            binding,
            {
                "binding_digest": _bytes(proof.binding_revision_digest),
                "device_id": proof.device_id,
                "device_identity_digest": _bytes(proof.device_identity_digest),
                "capability_id": proof.capability_id,
                "driver_profile_digest": _bytes(proof.driver_profile_digest),
                "matrix_row_id": proof.matrix_row_id,
                "options_digest": _bytes(proof.options_digest),
                "device_purpose": proof.purpose,
                "authority_revision_id": proof.authority_revision_id,
            },
            ProblemCode.CAPABILITY_CHANGED,
        )
        expected_scope_digest = canonical_digest(
            {
                "database": binding["database"],
                "organization_id": binding["organization_id"],
                "site_id": binding["site_id"],
                "kind": binding["scope_kind"],
                "pos_configuration_id": binding["pos_configuration_id"],
            }
        )
        if expected_scope_digest != proof.scope_digest:
            raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)
        _check_window(binding, now, ProblemCode.CAPABILITY_CHANGED)
        _check_signer(
            connection,
            binding,
            purpose="binding_revision",
            now=now,
            code=ProblemCode.CAPABILITY_CHANGED,
        )

        profile = _one(
            connection,
            device_driver_profiles_table,
            device_driver_profiles_table.c.profile_id == proof.driver_profile_id,
            ProblemCode.CAPABILITY_CHANGED,
        )
        _require_values(
            profile,
            {
                "profile_digest": _bytes(proof.driver_profile_digest),
                "authority_revision_id": proof.authority_revision_id,
            },
            ProblemCode.CAPABILITY_CHANGED,
        )
        _check_capability(profile, proof)
        _check_window(profile, now, ProblemCode.CAPABILITY_CHANGED)
        _check_signer(
            connection,
            profile,
            purpose="driver_profile",
            now=now,
            code=ProblemCode.CAPABILITY_CHANGED,
        )

        matrix = _one(
            connection,
            hardware_certification_matrix_rows_table,
            hardware_certification_matrix_rows_table.c.row_id == proof.matrix_row_id,
            ProblemCode.CERTIFICATION_REQUIRED,
        )
        _require_values(
            matrix,
            {
                "matrix_row_digest": _bytes(proof.matrix_row_digest),
                "device_id": proof.device_id,
                "device_identity_digest": _bytes(proof.device_identity_digest),
                "driver_profile_digest": _bytes(proof.driver_profile_digest),
                "capability_id": proof.capability_id,
                "authority_revision_id": proof.authority_revision_id,
            },
            ProblemCode.CERTIFICATION_REQUIRED,
        )
        _check_window(matrix, now, ProblemCode.CERTIFICATION_REQUIRED)
        _check_signer(
            connection,
            matrix,
            purpose="certification_matrix",
            now=now,
            code=ProblemCode.CERTIFICATION_REQUIRED,
        )

        evidence = _one(
            connection,
            device_test_evidence_table,
            device_test_evidence_table.c.evidence_id == proof.test_evidence_id,
            ProblemCode.CERTIFICATION_REQUIRED,
        )
        _require_values(
            evidence,
            {
                "evidence_digest": _bytes(proof.test_evidence_digest),
                "revision_id": proof.binding_revision_id,
                "device_id": proof.device_id,
                "device_identity_digest": _bytes(proof.device_identity_digest),
                "driver_profile_digest": _bytes(proof.driver_profile_digest),
                "matrix_row_id": proof.matrix_row_id,
                "capability_id": proof.capability_id,
                "result": "passed",
                "authority_revision_id": proof.authority_revision_id,
            },
            ProblemCode.CERTIFICATION_REQUIRED,
        )
        _check_evidence_window(evidence, now)
        _check_signer(
            connection,
            evidence,
            purpose="device_test_evidence",
            now=now,
            code=ProblemCode.CERTIFICATION_REQUIRED,
        )

        pointer = _one(
            connection,
            device_binding_authority_state_table,
            device_binding_authority_state_table.c.active_revision_id
            == proof.binding_revision_id,
            ProblemCode.CERTIFICATION_REQUIRED,
        )
        _require_values(
            pointer,
            {
                "active_test_evidence_id": proof.test_evidence_id,
                "database": binding["database"],
                "organization_id": binding["organization_id"],
                "site_id": binding["site_id"],
                "scope_kind": binding["scope_kind"],
                "pos_configuration_id": binding["pos_configuration_id"],
                "device_purpose": proof.purpose,
                "status": "active",
            },
            ProblemCode.CERTIFICATION_REQUIRED,
        )

        for kind, subject_id, digest, code in (
            (
                RevocationSubjectKind.AUTHORITY_REVISION.value,
                proof.authority_revision_id,
                proof.authority_revision_digest,
                ProblemCode.CAPABILITY_CHANGED,
            ),
            (
                RevocationSubjectKind.BINDING_REVISION.value,
                proof.binding_revision_id,
                proof.binding_revision_digest,
                ProblemCode.CAPABILITY_CHANGED,
            ),
            (
                RevocationSubjectKind.DRIVER_PROFILE.value,
                proof.driver_profile_id,
                proof.driver_profile_digest,
                ProblemCode.CAPABILITY_CHANGED,
            ),
            (
                RevocationSubjectKind.CERTIFICATION_MATRIX_ROW.value,
                proof.matrix_row_id,
                proof.matrix_row_digest,
                ProblemCode.CERTIFICATION_REQUIRED,
            ),
            (
                RevocationSubjectKind.DEVICE_TEST_EVIDENCE.value,
                proof.test_evidence_id,
                proof.test_evidence_digest,
                ProblemCode.CERTIFICATION_REQUIRED,
            ),
        ):
            _check_revocation(
                connection,
                kind=kind,
                subject_id=subject_id,
                digest=digest,
                code=code,
            )


def _one(
    connection: Connection,
    table: Table,
    predicate: ColumnElement[bool],
    code: ProblemCode,
) -> RowMapping:
    row = connection.execute(select(table).where(predicate)).mappings().first()
    if row is None:
        raise SpoolAdmissionError(code)
    return row


def _check_subjects(connection: Connection, proof: AuthorityProof) -> None:
    rows = tuple(
        connection.execute(
            select(device_work_authority_proof_subjects_table).where(
                device_work_authority_proof_subjects_table.c.proof_id
                == proof.proof_id
            )
        ).mappings()
    )
    actual = {
        (
            str(row["subject_kind"]),
            str(row["subject_id"]),
            _digest(row["subject_digest"]),
        )
        for row in rows
    }
    expected = {
        (
            RevocationSubjectKind.AUTHORITY_REVISION.value,
            proof.authority_revision_id,
            proof.authority_revision_digest,
        ),
        (
            RevocationSubjectKind.BINDING_REVISION.value,
            proof.binding_revision_id,
            proof.binding_revision_digest,
        ),
        (
            RevocationSubjectKind.DRIVER_PROFILE.value,
            proof.driver_profile_id,
            proof.driver_profile_digest,
        ),
        (
            RevocationSubjectKind.CERTIFICATION_MATRIX_ROW.value,
            proof.matrix_row_id,
            proof.matrix_row_digest,
        ),
        (
            RevocationSubjectKind.DEVICE_TEST_EVIDENCE.value,
            proof.test_evidence_id,
            proof.test_evidence_digest,
        ),
    }
    if actual != expected:
        raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)


def _check_capability(profile: RowMapping, proof: AuthorityProof) -> None:
    raw = profile["capabilities"]
    if not isinstance(raw, str):
        raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)
    try:
        capabilities = json.loads(raw)
    except (TypeError, ValueError):
        raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED) from None
    if not isinstance(capabilities, list):
        raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)
    matches = [
        item
        for item in capabilities
        if isinstance(item, dict) and item.get("capability_id") == proof.capability_id
    ]
    expected = {
        "operation": proof.operation,
        "media_type": proof.media_type,
        "contract_major": proof.contract_major,
        "options_digest": proof.options_digest,
    }
    if len(matches) != 1 or any(
        matches[0].get(name) != value for name, value in expected.items()
    ):
        raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)


def _check_window(
    row: RowMapping, now: datetime, code: ProblemCode
) -> None:
    effective = _time(row.get("effective_at"), code)
    expires = row.get("expires_at")
    if now < effective or (expires is not None and now >= _time(expires, code)):
        raise SpoolAdmissionError(code)


def _check_evidence_window(row: RowMapping, now: datetime) -> None:
    tested = _time(row.get("tested_at"), ProblemCode.CERTIFICATION_REQUIRED)
    valid_until = row.get("valid_until")
    if now < tested or (
        valid_until is not None
        and now >= _time(valid_until, ProblemCode.CERTIFICATION_REQUIRED)
    ):
        raise SpoolAdmissionError(ProblemCode.CERTIFICATION_REQUIRED)


def _check_signer(
    connection: Connection,
    signed_row: RowMapping,
    *,
    purpose: str,
    now: datetime,
    code: ProblemCode,
) -> None:
    signer = _one(
        connection,
        device_authority_signer_keys_table,
        device_authority_signer_keys_table.c.key_id == signed_row["signer_key_id"],
        code,
    )
    if signer["purpose"] != purpose or signer["state"] != "active":
        raise SpoolAdmissionError(code)
    not_before = _time(signer.get("not_before"), code)
    not_after = signer.get("not_after")
    if now < not_before or (
        not_after is not None and now >= _time(not_after, code)
    ):
        raise SpoolAdmissionError(code)


def _check_revocation(
    connection: Connection,
    *,
    kind: str,
    subject_id: str,
    digest: str,
    code: ProblemCode,
) -> None:
    revoked = connection.execute(
        select(device_authority_revocations_table.c.revocation_id).where(
            device_authority_revocations_table.c.subject_kind == kind,
            device_authority_revocations_table.c.subject_id == subject_id,
            device_authority_revocations_table.c.subject_digest == _bytes(digest),
        )
    ).scalar_one_or_none()
    if revoked is not None:
        raise SpoolAdmissionError(code)


def _require_values(
    row: RowMapping, expected: Mapping[str, object], code: ProblemCode
) -> None:
    for name, value in expected.items():
        actual = row.get(name)
        if isinstance(value, bytes) and isinstance(actual, (bytearray, memoryview)):
            actual = bytes(actual)
        if actual != value:
            raise SpoolAdmissionError(code)


def _bytes(value: str) -> bytes:
    return bytes.fromhex(value)


def _digest(value: object) -> str:
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) != 32:
        raise SpoolAdmissionError(ProblemCode.CAPABILITY_CHANGED)
    return bytes(value).hex()


def _time(value: object, code: ProblemCode) -> datetime:
    if not isinstance(value, str):
        raise SpoolAdmissionError(code)
    try:
        return parse_timestamp(value)
    except (TypeError, ValueError):
        raise SpoolAdmissionError(code) from None


def _integer(value: object, code: ProblemCode) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpoolAdmissionError(code)
    return value


__all__ = ["ActiveAuthorityGuard", "SqlActiveAuthorityGuard"]
