from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .bundle import AuthorityManifest

from .models import (
    AuthorityState,
    RevocationSubjectKind,
    SignedBindingRevision,
    SignerPurpose,
    SignerRecord,
    SignedDeviceObservation,
    SignedDeviceTestEvidence,
    SignedDriverProfile,
    SignedHardwareCertificationMatrixRow,
)


class AuthorityProjectionReader(Protocol):
    """Read-only seam for the Controller or Odoo authority projections."""

    def read_authority_state(self) -> AuthorityState | None: ...

    def read_manifest(self, revision_id: str) -> AuthorityManifest | None: ...

    def read_binding_revision(
        self, revision_id: str
    ) -> SignedBindingRevision | None: ...

    def read_active_test_evidence_id(self, revision_id: str) -> str | None: ...

    def read_driver_profile(self, digest: str) -> SignedDriverProfile | None: ...

    def read_certification_row(
        self, row_id: str
    ) -> SignedHardwareCertificationMatrixRow | None: ...

    def read_device_test(self, evidence_id: str) -> SignedDeviceTestEvidence | None: ...

    def read_signer(
        self, key_id: str, purpose: SignerPurpose
    ) -> SignerRecord | None: ...

    def is_revoked(
        self,
        subject_kind: RevocationSubjectKind,
        subject_id: str,
        subject_digest: str,
    ) -> bool: ...


class DeviceObservationReader(Protocol):
    """Trusted read seam for the current Agent Device observation."""

    def read_current(
        self, device_id: str, driver_profile_digest: str
    ) -> SignedDeviceObservation | None: ...
