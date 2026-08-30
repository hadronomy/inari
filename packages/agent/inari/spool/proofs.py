from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, RowMapping

from ..core.failures import ProblemCode
from ..db.schema import (
    device_work_authority_proof_subjects_table,
    device_work_authority_proofs_table,
)
from ..device_authority import AuthorityProof, RevocationSubjectKind
from .errors import SpoolAdmissionError


def insert_authority_proof(
    connection: Connection,
    *,
    admission_id: str,
    proof: AuthorityProof,
    grant_id: str | None = None,
    grant_pairing_id: str | None = None,
    grant_generation: int | None = None,
    grant_authorization_digest: bytes | None = None,
) -> None:
    """Store one immutable content-free proof and its revocation subjects."""

    values = _proof_values(
        admission_id,
        proof,
        grant_id=grant_id,
        grant_pairing_id=grant_pairing_id,
        grant_generation=grant_generation,
        grant_authorization_digest=grant_authorization_digest,
    )
    existing = (
        connection.execute(
            select(device_work_authority_proofs_table).where(
                device_work_authority_proofs_table.c.admission_id == admission_id
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        if {name: existing[name] for name in values} != values:
            raise SpoolAdmissionError(ProblemCode.IDEMPOTENCY_CONFLICT)
        return

    connection.execute(insert(device_work_authority_proofs_table).values(**values))
    connection.execute(
        insert(device_work_authority_proof_subjects_table),
        _proof_subject_values(proof),
    )


def read_authority_proof(
    connection: Connection,
    *,
    admission_id: str,
) -> AuthorityProof:
    row = (
        connection.execute(
            select(device_work_authority_proofs_table).where(
                device_work_authority_proofs_table.c.admission_id == admission_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise SpoolAdmissionError(ProblemCode.RECOVERY_UNCERTAIN)
    try:
        return _proof_from_row(row)
    except (TypeError, ValueError, KeyError):
        raise SpoolAdmissionError(ProblemCode.RECOVERY_UNCERTAIN) from None


def _proof_values(
    admission_id: str,
    proof: AuthorityProof,
    *,
    grant_id: str | None,
    grant_pairing_id: str | None,
    grant_generation: int | None,
    grant_authorization_digest: bytes | None,
) -> dict[str, object]:
    return {
        "proof_id": proof.proof_id,
        "admission_id": admission_id,
        "authority_revision_id": proof.authority_revision_id,
        "authority_revision_number": proof.authority_revision_number,
        "authority_revision_digest": bytes.fromhex(proof.authority_revision_digest),
        "snapshot_digest": bytes.fromhex(proof.snapshot_digest),
        "graph_digest": bytes.fromhex(proof.graph_digest),
        "scope_digest": bytes.fromhex(proof.scope_digest),
        "observation_digest": bytes.fromhex(proof.observation_digest),
        "binding_revision_id": proof.binding_revision_id,
        "binding_revision_digest": bytes.fromhex(proof.binding_revision_digest),
        "driver_profile_id": proof.driver_profile_id,
        "driver_profile_digest": bytes.fromhex(proof.driver_profile_digest),
        "matrix_row_id": proof.matrix_row_id,
        "matrix_row_digest": bytes.fromhex(proof.matrix_row_digest),
        "test_evidence_id": proof.test_evidence_id,
        "test_evidence_digest": bytes.fromhex(proof.test_evidence_digest),
        "device_id": proof.device_id,
        "device_identity_digest": bytes.fromhex(proof.device_identity_digest),
        "capability_id": proof.capability_id,
        "device_purpose": proof.purpose,
        "operation": proof.operation,
        "media_type": proof.media_type,
        "contract_major": proof.contract_major,
        "options_digest": bytes.fromhex(proof.options_digest),
        "issued_at": _timestamp(proof.issued_at),
        "valid_until": _timestamp(proof.valid_until),
        "grant_id": grant_id,
        "grant_pairing_id": grant_pairing_id,
        "grant_generation": grant_generation,
        "grant_authorization_digest": grant_authorization_digest,
    }


def _proof_subject_values(proof: AuthorityProof) -> list[dict[str, object]]:
    return [
        {
            "proof_id": proof.proof_id,
            "subject_kind": subject_kind.value,
            "subject_id": subject_id,
            "subject_digest": bytes.fromhex(subject_digest),
        }
        for subject_kind, subject_id, subject_digest in (
            (
                RevocationSubjectKind.AUTHORITY_REVISION,
                proof.authority_revision_id,
                proof.authority_revision_digest,
            ),
            (
                RevocationSubjectKind.BINDING_REVISION,
                proof.binding_revision_id,
                proof.binding_revision_digest,
            ),
            (
                RevocationSubjectKind.DRIVER_PROFILE,
                proof.driver_profile_id,
                proof.driver_profile_digest,
            ),
            (
                RevocationSubjectKind.CERTIFICATION_MATRIX_ROW,
                proof.matrix_row_id,
                proof.matrix_row_digest,
            ),
            (
                RevocationSubjectKind.DEVICE_TEST_EVIDENCE,
                proof.test_evidence_id,
                proof.test_evidence_digest,
            ),
        )
    ]


def _proof_from_row(row: RowMapping) -> AuthorityProof:
    return AuthorityProof(
        proof_id=str(row["proof_id"]),
        authority_revision_id=str(row["authority_revision_id"]),
        authority_revision_number=_integer(row["authority_revision_number"]),
        authority_revision_digest=_digest(row["authority_revision_digest"]),
        snapshot_digest=_digest(row["snapshot_digest"]),
        graph_digest=_digest(row["graph_digest"]),
        scope_digest=_digest(row["scope_digest"]),
        observation_digest=_digest(row["observation_digest"]),
        binding_revision_id=str(row["binding_revision_id"]),
        binding_revision_digest=_digest(row["binding_revision_digest"]),
        driver_profile_id=str(row["driver_profile_id"]),
        driver_profile_digest=_digest(row["driver_profile_digest"]),
        matrix_row_id=str(row["matrix_row_id"]),
        matrix_row_digest=_digest(row["matrix_row_digest"]),
        test_evidence_id=str(row["test_evidence_id"]),
        test_evidence_digest=_digest(row["test_evidence_digest"]),
        device_id=str(row["device_id"]),
        device_identity_digest=_digest(row["device_identity_digest"]),
        capability_id=str(row["capability_id"]),
        purpose=str(row["device_purpose"]),
        operation=str(row["operation"]),
        media_type=str(row["media_type"]),
        contract_major=_integer(row["contract_major"]),
        options_digest=_digest(row["options_digest"]),
        issued_at=_parse_timestamp(row["issued_at"]),
        valid_until=_parse_timestamp(row["valid_until"]),
    )


def _digest(value: object) -> str:
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) != 32:
        raise ValueError("authority proof digest must contain 32 bytes")
    return bytes(value).hex()


def _timestamp(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("authority proof timestamp must be text")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("authority proof integer is invalid")
    return value


__all__ = ["insert_authority_proof", "read_authority_proof"]
