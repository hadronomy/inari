from __future__ import annotations

from datetime import UTC, datetime
import json

from sqlalchemy import select
from sqlalchemy.engine import RowMapping

from ..db.schema import (
    device_authority_revisions_table,
    device_authority_revocations_table,
    device_authority_signer_keys_table,
    device_authority_state_table,
    device_binding_authority_state_table,
    device_binding_revisions_table,
    device_driver_profiles_table,
    device_observations_table,
    device_test_evidence_table,
    hardware_certification_matrix_rows_table,
)
from ..runtime.store import RuntimeStore
from .models import (
    AuthorityRevision,
    AuthorityScope,
    AuthorityState,
    AuthorityStatus,
    BindingRevision,
    DeviceCapability,
    DeviceObservation,
    DeviceTestEvidence,
    DeviceTestResult,
    DriverProfile,
    HardwareCertificationMatrixRow,
    OutputEvidence,
    RevocationSubjectKind,
    ScopeKind,
    SignedAuthorityRevision,
    SignedBindingRevision,
    SignedDeviceObservation,
    SignedDeviceTestEvidence,
    SignedDriverProfile,
    SignedHardwareCertificationMatrixRow,
    SignerPurpose,
    SignerRecord,
    SignerState,
)


class SqliteDeviceAuthorityReader:
    """Read the immutable Device Capability projection from SQLite."""

    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def read_authority_state(self) -> AuthorityState | None:
        with self._store.connection() as connection:
            state = (
                connection.execute(
                    select(device_authority_state_table).where(
                        device_authority_state_table.c.singleton_id == 1
                    )
                )
                .mappings()
                .first()
            )
            if state is None or state["current_revision_id"] is None:
                return None
            revision = (
                connection.execute(
                    select(device_authority_revisions_table).where(
                        device_authority_revisions_table.c.revision_id
                        == state["current_revision_id"]
                    )
                )
                .mappings()
                .first()
            )
        if revision is None:
            return None
        return AuthorityState(
            status=AuthorityStatus(str(state["status"])),
            current_revision=_signed_authority_revision(revision),
        )

    def read_binding_revision(self, revision_id: str) -> SignedBindingRevision | None:
        row = self._one(
            select(device_binding_revisions_table).where(
                device_binding_revisions_table.c.revision_id == revision_id
            )
        )
        return None if row is None else _signed_binding(row)

    def read_active_test_evidence_id(self, revision_id: str) -> str | None:
        with self._store.connection() as connection:
            value = connection.execute(
                select(
                    device_binding_authority_state_table.c.active_test_evidence_id
                ).where(
                    device_binding_authority_state_table.c.active_revision_id
                    == revision_id,
                    device_binding_authority_state_table.c.status == "active",
                )
            ).scalar_one_or_none()
        return None if value is None else str(value)

    def read_driver_profile(self, digest: str) -> SignedDriverProfile | None:
        row = self._one(
            select(device_driver_profiles_table).where(
                device_driver_profiles_table.c.profile_digest == bytes.fromhex(digest)
            )
        )
        return None if row is None else _signed_driver_profile(row)

    def read_certification_row(
        self, row_id: str
    ) -> SignedHardwareCertificationMatrixRow | None:
        row = self._one(
            select(hardware_certification_matrix_rows_table).where(
                hardware_certification_matrix_rows_table.c.row_id == row_id
            )
        )
        return None if row is None else _signed_matrix_row(row)

    def read_device_test(self, evidence_id: str) -> SignedDeviceTestEvidence | None:
        row = self._one(
            select(device_test_evidence_table).where(
                device_test_evidence_table.c.evidence_id == evidence_id
            )
        )
        return None if row is None else _signed_test_evidence(row)

    def read_signer(self, key_id: str, purpose: SignerPurpose) -> SignerRecord | None:
        row = self._one(
            select(device_authority_signer_keys_table).where(
                device_authority_signer_keys_table.c.key_id == key_id,
                device_authority_signer_keys_table.c.purpose == purpose.value,
            )
        )
        if row is None:
            return None
        return SignerRecord(
            key_id=str(row["key_id"]),
            purpose=SignerPurpose(str(row["purpose"])),
            public_key=bytes(row["public_key"]),
            state=SignerState(str(row["state"])),
            not_before=_timestamp(row["not_before"]),
            not_after=_optional_timestamp(row["not_after"]),
            retired_at=_optional_timestamp(row["retired_at"]),
        )

    def is_revoked(
        self,
        subject_kind: RevocationSubjectKind,
        subject_id: str,
        subject_digest: str,
    ) -> bool:
        with self._store.connection() as connection:
            row = connection.execute(
                select(device_authority_revocations_table.c.revocation_id).where(
                    device_authority_revocations_table.c.subject_kind
                    == subject_kind.value,
                    device_authority_revocations_table.c.subject_id == subject_id,
                    device_authority_revocations_table.c.subject_digest
                    == bytes.fromhex(subject_digest),
                )
            ).first()
        return row is not None

    def read_current(self, device_id: str) -> SignedDeviceObservation | None:
        row = self._one(
            select(device_observations_table)
            .where(device_observations_table.c.device_id == device_id)
            .order_by(
                device_observations_table.c.observed_at.desc(),
                device_observations_table.c.observation_id.desc(),
            )
            .limit(1)
        )
        return None if row is None else _signed_observation(row)

    def _one(self, statement) -> RowMapping | None:
        with self._store.connection() as connection:
            return connection.execute(statement).mappings().first()


def _signed_authority_revision(row: RowMapping) -> SignedAuthorityRevision:
    return SignedAuthorityRevision(
        revision=AuthorityRevision(
            revision_id=str(row["revision_id"]),
            revision_number=int(row["revision_number"]),
            manifest_digest=_digest(row["manifest_digest"]),
            effective_at=_timestamp(row["effective_at"]),
            expires_at=_optional_timestamp(row["expires_at"]),
        ),
        digest=_digest(row["revision_digest"]),
        signer_key_id=str(row["signer_key_id"]),
        signature=bytes(row["signature"]),
    )


def _signed_binding(row: RowMapping) -> SignedBindingRevision:
    return SignedBindingRevision(
        revision=BindingRevision(
            binding_id=str(row["binding_id"]),
            revision_id=str(row["revision_id"]),
            revision_number=int(row["revision_number"]),
            scope=AuthorityScope(
                database=str(row["database"]),
                organization_id=str(row["organization_id"]),
                site_id=str(row["site_id"]),
                kind=ScopeKind(str(row["scope_kind"])),
                pos_configuration_id=(
                    None
                    if row["pos_configuration_id"] is None
                    else str(row["pos_configuration_id"])
                ),
            ),
            purpose=str(row["device_purpose"]),
            device_id=str(row["device_id"]),
            device_identity_digest=_digest(row["device_identity_digest"]),
            capability_id=str(row["capability_id"]),
            driver_profile_digest=_digest(row["driver_profile_digest"]),
            matrix_row_id=str(row["matrix_row_id"]),
            options_digest=_digest(row["options_digest"]),
        ),
        digest=_digest(row["binding_digest"]),
        signer_key_id=str(row["signer_key_id"]),
        signature=bytes(row["signature"]),
    )


def _signed_driver_profile(row: RowMapping) -> SignedDriverProfile:
    raw_capabilities = json.loads(str(row["capabilities"]))
    if not isinstance(raw_capabilities, list):
        raise ValueError("Driver Profile capabilities must be a JSON array.")
    capabilities = tuple(
        DeviceCapability(
            capability_id=str(value["capability_id"]),
            operation=str(value["operation"]),
            media_type=str(value["media_type"]),
            contract_major=int(value["contract_major"]),
            output_evidence=OutputEvidence(str(value["output_evidence"])),
            max_payload_bytes=int(value["max_payload_bytes"]),
            max_copies=int(value["max_copies"]),
            options_digest=str(value["options_digest"]),
        )
        for value in raw_capabilities
        if isinstance(value, dict)
    )
    if len(capabilities) != len(raw_capabilities):
        raise ValueError("Driver Profile capabilities contain an invalid item.")
    return SignedDriverProfile(
        profile=DriverProfile(
            profile_id=str(row["profile_id"]),
            version=str(row["version"]),
            driver_id=str(row["driver_id"]),
            min_agent_version=str(row["min_agent_version"]),
            capabilities=capabilities,
            effective_at=_timestamp(row["effective_at"]),
            expires_at=_optional_timestamp(row["expires_at"]),
        ),
        digest=_digest(row["profile_digest"]),
        signer_key_id=str(row["signer_key_id"]),
        signature=bytes(row["signature"]),
    )


def _signed_matrix_row(row: RowMapping) -> SignedHardwareCertificationMatrixRow:
    return SignedHardwareCertificationMatrixRow(
        row=HardwareCertificationMatrixRow(
            row_id=str(row["row_id"]),
            version=int(row["version"]),
            device_id=str(row["device_id"]),
            device_identity_digest=_digest(row["device_identity_digest"]),
            manufacturer=str(row["manufacturer"]),
            model=str(row["model"]),
            firmware_version=str(row["firmware_version"]),
            firmware_build=str(row["firmware_build"]),
            driver_id=str(row["driver_id"]),
            driver_profile_digest=_digest(row["driver_profile_digest"]),
            capability_id=str(row["capability_id"]),
            platform_backend_id=str(row["platform_backend_id"]),
            connection=str(row["connection"]),
            media_profile=str(row["media_profile"]),
            operating_system=str(row["operating_system"]),
            release_set_id=str(row["release_set_id"]),
            effective_at=_timestamp(row["effective_at"]),
            expires_at=_optional_timestamp(row["expires_at"]),
        ),
        digest=_digest(row["matrix_row_digest"]),
        signer_key_id=str(row["signer_key_id"]),
        signature=bytes(row["signature"]),
    )


def _signed_test_evidence(row: RowMapping) -> SignedDeviceTestEvidence:
    return SignedDeviceTestEvidence(
        evidence=DeviceTestEvidence(
            evidence_id=str(row["evidence_id"]),
            revision_id=str(row["revision_id"]),
            device_id=str(row["device_id"]),
            device_identity_digest=_digest(row["device_identity_digest"]),
            capability_id=str(row["capability_id"]),
            driver_profile_digest=_digest(row["driver_profile_digest"]),
            matrix_row_id=str(row["matrix_row_id"]),
            output_evidence=OutputEvidence(str(row["output_evidence"])),
            result=DeviceTestResult(str(row["result"])),
            test_pattern_digest=_digest(row["test_pattern_digest"]),
            tested_at=_timestamp(row["tested_at"]),
            valid_until=_optional_timestamp(row["valid_until"]),
        ),
        digest=_digest(row["evidence_digest"]),
        signer_key_id=str(row["signer_key_id"]),
        signature=bytes(row["signature"]),
    )


def _signed_observation(row: RowMapping) -> SignedDeviceObservation:
    return SignedDeviceObservation(
        observation=DeviceObservation(
            observation_id=str(row["observation_id"]),
            device_id=str(row["device_id"]),
            device_identity_digest=_digest(row["device_identity_digest"]),
            driver_id=str(row["driver_id"]),
            driver_profile_digest=_digest(row["driver_profile_digest"]),
            platform_backend_id=str(row["platform_backend_id"]),
            connection=str(row["connection"]),
            media_profile=str(row["media_profile"]),
            firmware_version=str(row["firmware_version"]),
            firmware_build=str(row["firmware_build"]),
            operating_system=str(row["operating_system"]),
            ready=bool(row["ready"]),
            state=str(row["state"]),
            reason=None if row["reason"] is None else str(row["reason"]),
            observed_at=_timestamp(row["observed_at"]),
        ),
        digest=_digest(row["observation_digest"]),
        signer_key_id=str(row["signer_key_id"]),
        signature=bytes(row["signature"]),
    )


def _digest(value: object) -> str:
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) != 32:
        raise ValueError("Device Authority digest must contain 32 bytes.")
    return bytes(value).hex()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Device Authority timestamp must be text.")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _optional_timestamp(value: object) -> datetime | None:
    return None if value is None else _timestamp(value)


__all__ = ["SqliteDeviceAuthorityReader"]
