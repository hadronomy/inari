from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from inari.device_authority import (
    AdmissionPermit,
    AuthorityError,
    AuthorityProof,
    CapabilityAdmissionTarget,
)


def authority_proof() -> AuthorityProof:
    return AuthorityProof(
        proof_id="proof-1",
        authority_revision_id="authority-revision-1",
        authority_revision_number=1,
        authority_revision_digest="1" * 64,
        snapshot_digest="2" * 64,
        graph_digest="3" * 64,
        scope_digest="4" * 64,
        observation_digest="5" * 64,
        binding_revision_id="binding_revision_9",
        binding_revision_digest="6" * 64,
        driver_profile_id="driver-profile-1",
        driver_profile_digest="7" * 64,
        matrix_row_id="matrix-row-1",
        matrix_row_digest="8" * 64,
        test_evidence_id="device-test-1",
        test_evidence_digest="9" * 64,
        device_id="dev_receipt_1",
        device_identity_digest="a" * 64,
        capability_id="receipt-image-v1",
        purpose="pos_receipt",
        operation="receipt_image",
        media_type="image/jpeg",
        contract_major=1,
        options_digest=hashlib.sha256(b"{}").hexdigest(),
        issued_at=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2027, 1, 1, tzinfo=UTC),
    )


@dataclass(slots=True)
class StaticAdmissionAuthority:
    """Test adapter that records targets and issues one fixed proof."""

    proof: AuthorityProof = field(default_factory=authority_proof)
    error: AuthorityError | None = None
    targets: list[CapabilityAdmissionTarget] = field(default_factory=list)
    times: list[datetime | None] = field(default_factory=list)

    def authorize(
        self,
        target: CapabilityAdmissionTarget,
        *,
        now: datetime | None = None,
    ) -> AdmissionPermit:
        self.targets.append(target)
        self.times.append(now)
        if self.error is not None:
            raise self.error
        valid_until = self.proof.valid_until
        if target.requested_expires_at is not None:
            valid_until = min(valid_until, target.requested_expires_at)
        self.proof = replace(
            self.proof,
            binding_revision_id=target.binding_revision_id,
            device_id=target.device_id,
            purpose=target.purpose,
            operation=target.operation,
            media_type=target.media_type,
            contract_major=target.contract_major,
            options_digest=target.options_digest,
            valid_until=valid_until,
        )
        return AdmissionPermit._issue(self.proof, b"t" * 32)


__all__ = ["StaticAdmissionAuthority", "authority_proof"]
