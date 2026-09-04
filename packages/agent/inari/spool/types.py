from __future__ import annotations

from dataclasses import dataclass

from ..device_authority import AuthorityProof


@dataclass(frozen=True, slots=True)
class AdmissionManifest:
    scope_kind: str
    managed_work_id: str | None
    grant_id: str | None
    grant_pairing_id: str | None
    grant_generation: int | None
    idempotency_key: str
    database: str
    organization_id: str
    site_id: str
    pos_configuration_id: str | None
    paired_client_id: str | None
    actor_id: str
    device_id: str
    binding_revision_id: str
    authorization_digest: bytes
    operation: str
    media_type: str
    normalized_options_digest: bytes
    grant_scope_digest: bytes
    origin_submission_key: str
    origin_kind: str
    origin_json: str
    contract_major: int
    copy_ordinal: int
    intent_id: str
    fingerprint: bytes
    deadline_at: str
    original_size_bytes: int
    authority_proof: AuthorityProof


@dataclass(frozen=True, slots=True)
class AdmissionPlan:
    admission_id: str
    job_id: str
    reservation_id: str
    artifact_id: str
    manifest: AdmissionManifest


__all__ = ["AdmissionManifest", "AdmissionPlan"]
