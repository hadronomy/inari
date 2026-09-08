from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Never, Protocol
from uuid import uuid4

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

from .errors import AuthorityError, AuthorityErrorCode
from .models import (
    AdmissionPermit,
    AuthorityRevision,
    AuthorityScope,
    AuthorityState,
    AuthorityStatus,
    AuthorityProof,
    BindingRevision,
    CapabilityAdmissionTarget,
    CapabilityStreamTarget,
    DeviceCapability,
    DeviceObservation,
    DeviceTestEvidence,
    DeviceTestResult,
    DriverProfile,
    HardwareCertificationMatrixRow,
    OutputEvidence,
    RevocationSubjectKind,
    SignerPurpose,
    SignerRecord,
    SignerState,
    SignedDeviceObservation,
    SignedDeviceTestEvidence,
    SignedBindingRevision,
    SignedAuthorityRevision,
    SignedDriverProfile,
    SignedHardwareCertificationMatrixRow,
)
from .ports import AuthorityProjectionReader, DeviceObservationReader


class AdmissionAuthorizer(Protocol):
    """One-method seam for admission authorization."""

    def authorize(
        self, target: CapabilityAdmissionTarget, *, now: datetime | None = None
    ) -> AdmissionPermit: ...


class PreIoCapabilityChecker(Protocol):
    """One-method seam that must run immediately before Device I/O."""

    def check(
        self, permit: AdmissionPermit, *, now: datetime | None = None
    ) -> AuthorityProof: ...


class PreIoCapabilityGate:
    """Expose the pre-I/O check as a narrow seam for Device executors."""

    def __init__(self, authority: DeviceCapabilityAuthority) -> None:
        self._authority = authority

    def check(
        self, permit: AdmissionPermit, *, now: datetime | None = None
    ) -> AuthorityProof:
        return self._authority.check(permit, now=now)


class DeviceCapabilityAuthority:
    """Compile and enforce one exact, signed Device Capability graph."""

    def __init__(
        self,
        *,
        projections: AuthorityProjectionReader,
        observations: DeviceObservationReader,
        current_agent_version: str,
        permit_ttl: timedelta = timedelta(seconds=30),
        observation_max_age: timedelta = timedelta(seconds=5),
    ) -> None:
        if permit_ttl <= timedelta(0):
            raise ValueError("permit_ttl must be positive")
        if observation_max_age <= timedelta(0):
            raise ValueError("observation_max_age must be positive")
        try:
            self._current_agent_version = Version(current_agent_version)
        except InvalidVersion as exc:
            raise ValueError("current_agent_version must be a valid version") from exc
        self._projections = projections
        self._observations = observations
        self._permit_ttl = permit_ttl
        self._observation_max_age = observation_max_age
        self._issued_permits: dict[bytes, _IssuedPermit] = {}
        self._permit_lock = threading.RLock()

    def authorize(
        self, target: CapabilityAdmissionTarget, *, now: datetime | None = None
    ) -> AdmissionPermit:
        """Authorize Device Work after deriving and checking its complete graph."""

        at = _now(now)
        _ensure_target(target)
        authority_state = self._read_authority_state(at)
        signed_binding = self._read_binding(target.binding_revision_id)
        self._check_binding(signed_binding, target.binding_revision_id, at)
        binding = signed_binding.revision
        if binding.scope != target.scope:
            _reject(
                AuthorityErrorCode.SCOPE_MISMATCH,
                "The Binding Revision does not belong to this scope.",
            )
        self._check_binding_target(binding, target)
        profile = self._read_profile(binding.driver_profile_digest)
        self._check_profile(profile, binding.driver_profile_digest, at)
        capability = _find_capability(profile.profile, binding.capability_id)
        self._check_capability(capability, target)
        row = self._read_matrix(binding.matrix_row_id)
        self._check_matrix(row, profile.profile, binding, at)
        evidence_id = self._read_active_evidence_id(binding.revision_id)
        evidence = self._read_evidence(evidence_id)
        self._check_evidence(evidence, binding, capability, at)
        observation = self._read_observation(binding.device_id)
        self._check_observation(observation, row.row, profile.profile, at)
        self._check_revocations(signed_binding, profile, row, evidence)

        validity_candidates = [
            profile.profile.expires_at,
            row.row.expires_at,
            evidence.evidence.valid_until,
            authority_state.current_revision.revision.expires_at,
            target.requested_expires_at,
        ]
        valid_until = min(value for value in validity_candidates if value is not None)
        if target.requested_expires_at is not None:
            if target.requested_expires_at <= at:
                _reject(
                    AuthorityErrorCode.EXPIRED, "The requested permit deadline passed."
                )
        if valid_until <= at:
            _reject(AuthorityErrorCode.EXPIRED, "The permit deadline passed.")
        permit_expires_at = min(at + self._permit_ttl, valid_until)

        graph = _graph_payload(target, binding, capability, profile, row, evidence)
        scope_digest = _digest(_scope_payload(target.scope))
        graph_digest = _digest(graph)
        observation_digest = _digest(observation.observation)
        snapshot = _snapshot_payload(
            authority_revision_id=(
                authority_state.current_revision.revision.revision_id
            ),
            authority_revision_number=(
                authority_state.current_revision.revision.revision_number
            ),
            authority_revision_digest=authority_state.current_revision.digest,
            graph=graph,
            scope_digest=scope_digest,
            graph_digest=graph_digest,
            observation_digest=observation_digest,
            issued_at=at,
            valid_until=valid_until,
        )
        proof = AuthorityProof(
            proof_id=uuid4().hex,
            authority_revision_id=(
                authority_state.current_revision.revision.revision_id
            ),
            authority_revision_number=(
                authority_state.current_revision.revision.revision_number
            ),
            authority_revision_digest=authority_state.current_revision.digest,
            snapshot_digest=_digest(snapshot),
            graph_digest=graph_digest,
            scope_digest=scope_digest,
            observation_digest=observation_digest,
            binding_revision_id=binding.revision_id,
            binding_revision_digest=signed_binding.digest,
            driver_profile_id=profile.profile.profile_id,
            driver_profile_digest=profile.digest,
            matrix_row_id=row.row.row_id,
            matrix_row_digest=row.digest,
            test_evidence_id=evidence.evidence.evidence_id,
            test_evidence_digest=evidence.digest,
            device_id=binding.device_id,
            device_identity_digest=binding.device_identity_digest,
            capability_id=binding.capability_id,
            purpose=binding.purpose,
            operation=target.operation,
            media_type=target.media_type,
            contract_major=target.contract_major,
            options_digest=binding.options_digest,
            issued_at=at,
            valid_until=valid_until,
        )
        with self._permit_lock:
            self._purge_expired_permits(at)
            permit_token = secrets.token_bytes(32)
            while permit_token in self._issued_permits:
                permit_token = secrets.token_bytes(32)
            self._issued_permits[permit_token] = _IssuedPermit(
                proof=proof,
                expires_at=permit_expires_at,
            )
        return AdmissionPermit._issue(
            proof,
            permit_token,
        )

    def authorize_stream(
        self,
        target: CapabilityStreamTarget,
        *,
        now: datetime | None = None,
    ) -> AdmissionPermit:
        """Authorize an input stream without trusting caller-supplied graph facts."""

        if not isinstance(target, CapabilityStreamTarget):
            _reject(
                AuthorityErrorCode.INVALID_REQUEST,
                "The stream capability target is invalid.",
            )
        at = _now(now)
        signed_binding = self._read_binding(target.binding_revision_id)
        binding = signed_binding.revision
        signed_profile = self._read_profile(binding.driver_profile_digest)
        capability = _find_capability(
            signed_profile.profile,
            binding.capability_id,
        )
        return self.authorize(
            CapabilityAdmissionTarget(
                scope=target.scope,
                purpose=target.purpose,
                device_id=target.device_id,
                binding_revision_id=target.binding_revision_id,
                operation=target.operation,
                media_type=capability.media_type,
                contract_major=target.contract_major,
                options_digest=binding.options_digest,
            ),
            now=at,
        )

    def check(
        self, permit: AdmissionPermit, *, now: datetime | None = None
    ) -> AuthorityProof:
        """Recheck the permit, revocations, and current Device immediately before I/O."""

        if not isinstance(permit, AdmissionPermit):
            _reject(
                AuthorityErrorCode.INVALID_REQUEST, "The admission permit is invalid."
            )
        at = _now(now)
        permit_token = permit._token_for_authority_check()
        with self._permit_lock:
            issued = self._issued_permits.get(permit_token)
        if issued is None or issued.proof != permit.authority_proof:
            _reject(
                AuthorityErrorCode.INVALID_REQUEST, "The admission permit is invalid."
            )
        proof = issued.proof
        if at < proof.issued_at or at >= issued.expires_at:
            with self._permit_lock:
                self._issued_permits.pop(permit_token, None)
            _reject(AuthorityErrorCode.EXPIRED, "The admission permit is not current.")
        return self.check_proof(proof, now=at)

    def check_proof(
        self,
        proof: AuthorityProof,
        *,
        now: datetime | None = None,
    ) -> AuthorityProof:
        """Recheck durable authority proof immediately before Device I/O."""

        if not isinstance(proof, AuthorityProof):
            _reject(
                AuthorityErrorCode.INVALID_REQUEST, "The authority proof is invalid."
            )
        at = _now(now)
        if at < proof.issued_at or at >= proof.valid_until:
            _reject(AuthorityErrorCode.EXPIRED, "The authority proof is not current.")
        current_authority_state = self._read_authority_state(at)
        current_revision = current_authority_state.current_revision.revision
        if current_revision.revision_number < proof.authority_revision_number:
            _reject(
                AuthorityErrorCode.AUTHORITY_UNAVAILABLE,
                "The Device Capability authority revision moved backwards.",
            )
        if current_revision.revision_number == proof.authority_revision_number and (
            current_revision.revision_id != proof.authority_revision_id
            or current_authority_state.current_revision.digest
            != proof.authority_revision_digest
        ):
            _reject(
                AuthorityErrorCode.SIGNATURE_INVALID,
                "The Device Capability authority revision changed in place.",
            )
        if self._projections.is_revoked(
            RevocationSubjectKind.AUTHORITY_REVISION,
            proof.authority_revision_id,
            proof.authority_revision_digest,
        ):
            _reject(
                AuthorityErrorCode.REVOKED,
                "The admission authority revision is revoked.",
            )
        signed_binding = self._read_binding(proof.binding_revision_id)
        binding = signed_binding.revision
        target = CapabilityAdmissionTarget(
            scope=binding.scope,
            purpose=proof.purpose,
            device_id=proof.device_id,
            binding_revision_id=proof.binding_revision_id,
            operation=proof.operation,
            media_type=proof.media_type,
            contract_major=proof.contract_major,
            options_digest=proof.options_digest,
            requested_expires_at=proof.valid_until,
        )
        self._check_binding(signed_binding, target.binding_revision_id, at)
        if (
            signed_binding.digest != proof.binding_revision_digest
            or _digest(_scope_payload(binding.scope)) != proof.scope_digest
            or binding.device_identity_digest != proof.device_identity_digest
            or binding.capability_id != proof.capability_id
        ):
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The durable Binding Revision proof differs.",
            )
        self._check_binding_target(binding, target)
        self._check_active_evidence(binding.revision_id, proof.test_evidence_id)
        profile = self._read_profile(binding.driver_profile_digest)
        if (
            profile.digest != proof.driver_profile_digest
            or profile.profile.profile_id != proof.driver_profile_id
        ):
            _reject(AuthorityErrorCode.GRAPH_MISMATCH, "The Driver Profile changed.")
        self._check_profile(profile, binding.driver_profile_digest, at)
        capability = _find_capability(profile.profile, binding.capability_id)
        self._check_capability(capability, target)
        row = self._read_matrix(binding.matrix_row_id)
        if (
            row.row.row_id != proof.matrix_row_id
            or row.digest != proof.matrix_row_digest
        ):
            _reject(AuthorityErrorCode.GRAPH_MISMATCH, "The certification row changed.")
        self._check_matrix(
            row,
            profile.profile,
            binding,
            at,
        )
        evidence = self._read_evidence(proof.test_evidence_id)
        if evidence.digest != proof.test_evidence_digest:
            _reject(AuthorityErrorCode.GRAPH_MISMATCH, "The Device Test changed.")
        self._check_evidence(
            evidence,
            binding,
            capability,
            at,
        )
        self._check_revocations(
            signed_binding,
            profile,
            row,
            evidence,
        )
        graph = _graph_payload(target, binding, capability, profile, row, evidence)
        if _digest(graph) != proof.graph_digest:
            _reject(AuthorityErrorCode.GRAPH_MISMATCH, "The authority graph changed.")
        snapshot = _snapshot_payload(
            authority_revision_id=proof.authority_revision_id,
            authority_revision_number=proof.authority_revision_number,
            authority_revision_digest=proof.authority_revision_digest,
            graph=graph,
            scope_digest=proof.scope_digest,
            graph_digest=proof.graph_digest,
            observation_digest=proof.observation_digest,
            issued_at=proof.issued_at,
            valid_until=proof.valid_until,
        )
        if _digest(snapshot) != proof.snapshot_digest:
            _reject(AuthorityErrorCode.GRAPH_MISMATCH, "The authority proof changed.")
        observation = self._read_observation(proof.device_id)
        self._check_observation(
            observation,
            row.row,
            profile.profile,
            at,
        )
        return proof

    def _read_authority_state(self, at: datetime) -> AuthorityState:
        state = self._projections.read_authority_state()
        if state is None or state.status is not AuthorityStatus.READY:
            _reject(
                AuthorityErrorCode.AUTHORITY_UNAVAILABLE,
                "The Device Capability authority is not ready.",
            )
        self._verify_signed(
            payload=state.current_revision.revision,
            digest=state.current_revision.digest,
            signer_key_id=state.current_revision.signer_key_id,
            signature=state.current_revision.signature,
            purpose=SignerPurpose.AUTHORITY_REVISION,
            now=at,
        )
        revision = state.current_revision.revision
        if at < revision.effective_at or (
            revision.expires_at is not None and at >= revision.expires_at
        ):
            _reject(
                AuthorityErrorCode.AUTHORITY_UNAVAILABLE,
                "The Device Capability authority revision is not current.",
            )
        self._check_authority_revision_revocation(state.current_revision)
        return state

    def _check_authority_revision_revocation(
        self,
        revision: SignedAuthorityRevision,
    ) -> None:
        if self._projections.is_revoked(
            RevocationSubjectKind.AUTHORITY_REVISION,
            revision.revision.revision_id,
            revision.digest,
        ):
            _reject(
                AuthorityErrorCode.REVOKED,
                "The Device Capability authority revision is revoked.",
            )

    def _purge_expired_permits(self, now: datetime) -> None:
        expired = [
            token
            for token, issued in self._issued_permits.items()
            if issued.expires_at <= now
        ]
        for token in expired:
            self._issued_permits.pop(token, None)

    def _read_binding(self, revision_id: str) -> SignedBindingRevision:
        binding = self._projections.read_binding_revision(revision_id)
        if binding is None:
            _reject(
                AuthorityErrorCode.NOT_FOUND, "The Binding Revision is not available."
            )
        return binding

    def _check_binding(
        self,
        signed: SignedBindingRevision,
        revision_id: str,
        at: datetime,
    ) -> None:
        self._verify_signed(
            payload=signed.revision,
            digest=signed.digest,
            signer_key_id=signed.signer_key_id,
            signature=signed.signature,
            purpose=SignerPurpose.BINDING_REVISION,
            now=at,
        )
        if signed.revision.revision_id != revision_id:
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The signed Binding Revision identity differs.",
            )

    def _read_profile(self, digest: str) -> SignedDriverProfile:
        profile = self._projections.read_driver_profile(digest)
        if profile is None:
            _reject(
                AuthorityErrorCode.NOT_FOUND, "The Driver Profile is not available."
            )
        return profile

    def _read_matrix(self, row_id: str) -> SignedHardwareCertificationMatrixRow:
        row = self._projections.read_certification_row(row_id)
        if row is None:
            _reject(
                AuthorityErrorCode.CERTIFICATION_REQUIRED,
                "The exact Hardware Certification Matrix Row is not available.",
            )
        return row

    def _read_evidence(self, evidence_id: str) -> SignedDeviceTestEvidence:
        evidence = self._projections.read_device_test(evidence_id)
        if evidence is None:
            _reject(
                AuthorityErrorCode.TEST_REQUIRED,
                "A current signed Device Test is required.",
            )
        return evidence

    def _read_active_evidence_id(self, revision_id: str) -> str:
        evidence_id = self._projections.read_active_test_evidence_id(revision_id)
        if evidence_id is None:
            _reject(
                AuthorityErrorCode.TEST_REQUIRED,
                "An active Device Test is required for this Binding Revision.",
            )
        return evidence_id

    def _check_active_evidence(self, revision_id: str, evidence_id: str) -> None:
        if self._read_active_evidence_id(revision_id) != evidence_id:
            _reject(
                AuthorityErrorCode.TEST_REQUIRED,
                "The active Device Test does not match this Binding Revision.",
            )

    def _read_observation(self, device_id: str) -> SignedDeviceObservation:
        observation = self._observations.read_current(device_id)
        if observation is None:
            _reject(
                AuthorityErrorCode.OBSERVATION_UNAVAILABLE,
                "The current Device observation is not available.",
            )
        return observation

    def _check_binding_target(
        self, binding: BindingRevision, target: CapabilityAdmissionTarget
    ) -> None:
        expected = {
            "purpose": binding.purpose,
            "device_id": binding.device_id,
            "binding_revision_id": binding.revision_id,
            "options_digest": binding.options_digest,
        }
        actual = {name: getattr(target, name) for name in expected}
        if actual != expected:
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The request graph does not match the Binding Revision.",
            )

    def _check_profile(
        self, signed: SignedDriverProfile, expected_digest: str, at: datetime
    ) -> None:
        self._verify_signed(
            payload=signed.profile,
            digest=signed.digest,
            signer_key_id=signed.signer_key_id,
            signature=signed.signature,
            purpose=SignerPurpose.DRIVER_PROFILE,
            now=at,
        )
        if signed.digest != expected_digest:
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH, "The Driver Profile digest differs."
            )
        try:
            minimum_agent_version = Version(signed.profile.min_agent_version)
        except InvalidVersion:
            _reject(
                AuthorityErrorCode.SIGNATURE_INVALID,
                "The Driver Profile minimum Agent version is invalid.",
            )
        if self._current_agent_version < minimum_agent_version:
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The Driver Profile requires a newer Agent version.",
            )
        if at < signed.profile.effective_at or (
            signed.profile.expires_at is not None and at >= signed.profile.expires_at
        ):
            _reject(AuthorityErrorCode.EXPIRED, "The Driver Profile is not current.")

    @staticmethod
    def _check_capability(
        capability: DeviceCapability, target: CapabilityAdmissionTarget
    ) -> None:
        if (
            capability.operation != target.operation
            or capability.media_type != target.media_type
            or capability.contract_major != target.contract_major
            or capability.options_digest != target.options_digest
        ):
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The requested Device Capability contract differs.",
            )

    def _check_matrix(
        self,
        signed: SignedHardwareCertificationMatrixRow,
        profile: DriverProfile,
        binding: BindingRevision,
        at: datetime,
    ) -> None:
        self._verify_signed(
            payload=signed.row,
            digest=signed.digest,
            signer_key_id=signed.signer_key_id,
            signature=signed.signature,
            purpose=SignerPurpose.CERTIFICATION_MATRIX,
            now=at,
        )
        row = signed.row
        if (
            row.device_id != binding.device_id
            or row.device_identity_digest != binding.device_identity_digest
            or row.driver_profile_digest != binding.driver_profile_digest
            or row.capability_id != binding.capability_id
            or row.driver_id != profile.driver_id
        ):
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The certification row does not match the Driver Profile and binding.",
            )
        if at < row.effective_at or (
            row.expires_at is not None and at >= row.expires_at
        ):
            _reject(
                AuthorityErrorCode.CERTIFICATION_REQUIRED,
                "The certification row is not current.",
            )

    def _check_evidence(
        self,
        signed: SignedDeviceTestEvidence,
        binding: BindingRevision,
        capability: DeviceCapability,
        at: datetime,
    ) -> None:
        self._verify_signed(
            payload=signed.evidence,
            digest=signed.digest,
            signer_key_id=signed.signer_key_id,
            signature=signed.signature,
            purpose=SignerPurpose.DEVICE_TEST_EVIDENCE,
            now=at,
        )
        evidence = signed.evidence
        if evidence.result is not DeviceTestResult.PASSED:
            _reject(
                AuthorityErrorCode.TEST_REQUIRED,
                "A passed Device Test is required.",
            )
        if (
            evidence.revision_id != binding.revision_id
            or evidence.device_id != binding.device_id
            or evidence.device_identity_digest != binding.device_identity_digest
            or evidence.capability_id != binding.capability_id
            or evidence.driver_profile_digest != binding.driver_profile_digest
            or evidence.matrix_row_id != binding.matrix_row_id
        ):
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The Device Test does not match the binding graph.",
            )
        if at < evidence.tested_at or (
            evidence.valid_until is not None and at >= evidence.valid_until
        ):
            _reject(AuthorityErrorCode.EXPIRED, "The Device Test is not current.")
        if _evidence_rank(evidence.output_evidence) < _evidence_rank(
            capability.output_evidence
        ):
            _reject(
                AuthorityErrorCode.GRAPH_MISMATCH,
                "The Device Test evidence is weaker than the capability contract.",
            )

    def _check_observation(
        self,
        signed: SignedDeviceObservation,
        row: HardwareCertificationMatrixRow,
        profile: DriverProfile,
        at: datetime,
    ) -> None:
        self._verify_signed(
            payload=signed.observation,
            digest=signed.digest,
            signer_key_id=signed.signer_key_id,
            signature=signed.signature,
            purpose=SignerPurpose.DEVICE_OBSERVATION,
            now=at,
        )
        observation = signed.observation
        if not observation.ready or observation.state != "ready":
            _reject(
                AuthorityErrorCode.DEVICE_NOT_READY,
                "The Device is not ready for Device Work.",
            )
        if (
            observation.observed_at > at
            or at - observation.observed_at > self._observation_max_age
        ):
            _reject(
                AuthorityErrorCode.OBSERVATION_DRIFT,
                "The Device observation is stale.",
            )
        if (
            observation.device_id != row.device_id
            or observation.device_identity_digest != row.device_identity_digest
            or observation.driver_id != row.driver_id
            or observation.driver_profile_digest != row.driver_profile_digest
            or observation.platform_backend_id != row.platform_backend_id
            or observation.connection != row.connection
            or observation.media_profile != row.media_profile
            or observation.firmware_version != row.firmware_version
            or observation.firmware_build != row.firmware_build
            or observation.operating_system != row.operating_system
            or profile.driver_id != observation.driver_id
        ):
            _reject(
                AuthorityErrorCode.OBSERVATION_DRIFT,
                "The current Device identity differs from certification.",
            )

    def _check_revocations(
        self,
        binding: SignedBindingRevision,
        profile: SignedDriverProfile,
        row: SignedHardwareCertificationMatrixRow,
        evidence: SignedDeviceTestEvidence,
    ) -> None:
        for subject_kind, subject_id, subject_digest in (
            (
                RevocationSubjectKind.BINDING_REVISION,
                binding.revision.revision_id,
                binding.digest,
            ),
            (
                RevocationSubjectKind.DRIVER_PROFILE,
                profile.profile.profile_id,
                profile.digest,
            ),
            (
                RevocationSubjectKind.CERTIFICATION_MATRIX_ROW,
                row.row.row_id,
                row.digest,
            ),
            (
                RevocationSubjectKind.DEVICE_TEST_EVIDENCE,
                evidence.evidence.evidence_id,
                evidence.digest,
            ),
        ):
            if self._projections.is_revoked(
                subject_kind,
                subject_id,
                subject_digest,
            ):
                _reject(
                    AuthorityErrorCode.REVOKED,
                    "A required Device Capability authority record is revoked.",
                )

    def _verify_signed(
        self,
        *,
        payload: object,
        digest: str,
        signer_key_id: str,
        signature: bytes,
        purpose: SignerPurpose,
        now: datetime,
    ) -> None:
        verify_signed(
            payload=payload,
            digest=digest,
            signer=self._projections.read_signer(signer_key_id, purpose),
            signature=signature,
            purpose=purpose,
            now=now,
        )


def verify_signed(
    *,
    payload: object,
    digest: str,
    signer: SignerRecord | None,
    signature: bytes,
    purpose: SignerPurpose,
    now: datetime,
) -> None:
    """Verify a record against an independently trusted, purpose-bound key."""
    if _digest(payload) != digest:
        _reject(
            AuthorityErrorCode.SIGNATURE_INVALID,
            "The signed digest does not match.",
        )
    if signer is None:
        _reject(AuthorityErrorCode.SIGNATURE_INVALID, "The signing key is not trusted.")
    if signer.purpose is not purpose:
        _reject(
            AuthorityErrorCode.SIGNER_PURPOSE_MISMATCH,
            "The signing key has a different purpose.",
        )
    if (
        signer.state is not SignerState.ACTIVE
        or now < signer.not_before
        or (signer.not_after is not None and now >= signer.not_after)
    ):
        _reject(AuthorityErrorCode.REVOKED, "The signing key is not active.")
    try:
        Ed25519PublicKey.from_public_bytes(signer.public_key).verify(
            signature,
            _canonical_payload(payload),
        )
    except (InvalidSignature, ValueError):
        _reject(AuthorityErrorCode.SIGNATURE_INVALID, "The signature is invalid.")


@dataclass(frozen=True, slots=True)
class _IssuedPermit:
    proof: AuthorityProof
    expires_at: datetime


def _ensure_target(target: CapabilityAdmissionTarget) -> None:
    if not isinstance(target, CapabilityAdmissionTarget):
        _reject(AuthorityErrorCode.INVALID_REQUEST, "The admission target is invalid.")


def _find_capability(profile: DriverProfile, capability_id: str) -> DeviceCapability:
    for capability in profile.capabilities:
        if capability.capability_id == capability_id:
            return capability
    _reject(
        AuthorityErrorCode.GRAPH_MISMATCH,
        "The Device Capability is not in the profile.",
    )


def _evidence_rank(evidence: OutputEvidence) -> int:
    return {
        OutputEvidence.TRANSPORT: 1,
        OutputEvidence.SPOOLER: 2,
        OutputEvidence.DEVICE: 3,
    }[evidence]


def _now(value: datetime | None) -> datetime:
    at = value or datetime.now(tz=UTC)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return at.astimezone(UTC)


def _reject(code: AuthorityErrorCode, message: str) -> Never:
    raise AuthorityError(code, message)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_payload(value)).hexdigest()


def canonical_digest(value: object) -> str:
    """Return the SHA-256 digest of RFC 8785 canonical JSON."""

    return _digest(value)


def canonical_json_bytes(value: object) -> bytes:
    """Return the RFC 8785 JSON representation used for signatures."""

    return _canonical_payload(value)


def _canonical_payload(value: object) -> bytes:
    return rfc8785.dumps(_payload(value))


def _payload(value: object) -> Mapping[str, object]:
    if isinstance(value, AuthorityRevision):
        return authority_revision_payload(value)
    if isinstance(value, DriverProfile):
        return driver_profile_payload(value)
    if isinstance(value, BindingRevision):
        return binding_revision_payload(value)
    if isinstance(value, HardwareCertificationMatrixRow):
        return certification_row_payload(value)
    if isinstance(value, DeviceTestEvidence):
        return device_test_payload(value)
    if isinstance(value, DeviceObservation):
        return device_observation_payload(value)
    if isinstance(value, Mapping):
        return _mapping_payload(value)
    raise TypeError(f"unsupported signed payload: {type(value).__name__}")


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _mapping_payload(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _mapping_payload(value: Mapping[object, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("canonical JSON objects must use string keys")
        result[key] = _json_value(item)
    return result


def _iso(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _scope_payload(scope: AuthorityScope) -> dict[str, object]:
    return {
        "database": scope.database,
        "organization_id": scope.organization_id,
        "site_id": scope.site_id,
        "kind": scope.kind.value,
        "pos_configuration_id": scope.pos_configuration_id,
    }


def _capability_payload(capability: DeviceCapability) -> dict[str, object]:
    return {
        "capability_id": capability.capability_id,
        "operation": capability.operation,
        "media_type": capability.media_type,
        "contract_major": capability.contract_major,
        "output_evidence": capability.output_evidence.value,
        "max_payload_bytes": capability.max_payload_bytes,
        "max_copies": capability.max_copies,
        "options_digest": capability.options_digest,
    }


def driver_profile_payload(profile: DriverProfile) -> dict[str, object]:
    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "driver_id": profile.driver_id,
        "min_agent_version": profile.min_agent_version,
        "capabilities": [_capability_payload(item) for item in profile.capabilities],
        "effective_at": _iso(profile.effective_at),
        "expires_at": _iso(profile.expires_at) if profile.expires_at else None,
    }


def authority_revision_payload(revision: AuthorityRevision) -> dict[str, object]:
    return {
        "revision_id": revision.revision_id,
        "revision_number": revision.revision_number,
        "manifest_digest": revision.manifest_digest,
        "effective_at": _iso(revision.effective_at),
        "expires_at": _iso(revision.expires_at) if revision.expires_at else None,
    }


def binding_revision_payload(binding: BindingRevision) -> dict[str, object]:
    return {
        "binding_id": binding.binding_id,
        "revision_id": binding.revision_id,
        "revision_number": binding.revision_number,
        "scope": _scope_payload(binding.scope),
        "purpose": binding.purpose,
        "device_id": binding.device_id,
        "device_identity_digest": binding.device_identity_digest,
        "capability_id": binding.capability_id,
        "driver_profile_digest": binding.driver_profile_digest,
        "matrix_row_id": binding.matrix_row_id,
        "options_digest": binding.options_digest,
    }


def certification_row_payload(row: HardwareCertificationMatrixRow) -> dict[str, object]:
    return {
        "row_id": row.row_id,
        "version": row.version,
        "device_id": row.device_id,
        "device_identity_digest": row.device_identity_digest,
        "manufacturer": row.manufacturer,
        "model": row.model,
        "firmware_version": row.firmware_version,
        "firmware_build": row.firmware_build,
        "driver_id": row.driver_id,
        "driver_profile_digest": row.driver_profile_digest,
        "capability_id": row.capability_id,
        "platform_backend_id": row.platform_backend_id,
        "connection": row.connection,
        "media_profile": row.media_profile,
        "operating_system": row.operating_system,
        "release_set_id": row.release_set_id,
        "effective_at": _iso(row.effective_at),
        "expires_at": _iso(row.expires_at) if row.expires_at else None,
    }


def device_test_payload(evidence: DeviceTestEvidence) -> dict[str, object]:
    return {
        "evidence_id": evidence.evidence_id,
        "revision_id": evidence.revision_id,
        "device_id": evidence.device_id,
        "device_identity_digest": evidence.device_identity_digest,
        "capability_id": evidence.capability_id,
        "driver_profile_digest": evidence.driver_profile_digest,
        "matrix_row_id": evidence.matrix_row_id,
        "output_evidence": evidence.output_evidence.value,
        "result": evidence.result.value,
        "test_pattern_digest": evidence.test_pattern_digest,
        "tested_at": _iso(evidence.tested_at),
        "valid_until": _iso(evidence.valid_until) if evidence.valid_until else None,
    }


def device_observation_payload(observation: DeviceObservation) -> dict[str, object]:
    return {
        "observation_id": observation.observation_id,
        "device_id": observation.device_id,
        "device_identity_digest": observation.device_identity_digest,
        "driver_id": observation.driver_id,
        "driver_profile_digest": observation.driver_profile_digest,
        "platform_backend_id": observation.platform_backend_id,
        "connection": observation.connection,
        "media_profile": observation.media_profile,
        "firmware_version": observation.firmware_version,
        "firmware_build": observation.firmware_build,
        "operating_system": observation.operating_system,
        "ready": observation.ready,
        "state": observation.state,
        "reason": observation.reason,
        "observed_at": _iso(observation.observed_at),
    }


def _graph_payload(
    target: CapabilityAdmissionTarget,
    binding: BindingRevision,
    capability: DeviceCapability,
    profile: SignedDriverProfile,
    row: SignedHardwareCertificationMatrixRow,
    evidence: SignedDeviceTestEvidence,
) -> dict[str, object]:
    return {
        "target": {
            "purpose": target.purpose,
            "device_id": target.device_id,
            "binding_revision_id": target.binding_revision_id,
            "operation": target.operation,
            "media_type": target.media_type,
            "contract_major": target.contract_major,
            "options_digest": target.options_digest,
        },
        "binding": {
            "binding_id": binding.binding_id,
            "revision_id": binding.revision_id,
            "revision_number": binding.revision_number,
            "purpose": binding.purpose,
            "device_id": binding.device_id,
            "device_identity_digest": binding.device_identity_digest,
            "capability_id": binding.capability_id,
            "driver_profile_digest": binding.driver_profile_digest,
            "matrix_row_id": binding.matrix_row_id,
            "options_digest": binding.options_digest,
        },
        "binding_digest": canonical_digest(binding),
        "capability": _capability_payload(capability),
        "profile_digest": profile.digest,
        "matrix_row_digest": row.digest,
        "evidence_digest": evidence.digest,
    }


def _snapshot_payload(
    *,
    authority_revision_id: str,
    authority_revision_number: int,
    authority_revision_digest: str,
    graph: dict[str, object],
    scope_digest: str,
    graph_digest: str,
    observation_digest: str,
    issued_at: datetime,
    valid_until: datetime,
) -> dict[str, object]:
    return {
        "authority_revision_id": authority_revision_id,
        "authority_revision_number": authority_revision_number,
        "authority_revision_digest": authority_revision_digest,
        "scope_digest": scope_digest,
        "graph_digest": graph_digest,
        "observation_digest": observation_digest,
        "issued_at": _iso(issued_at),
        "valid_until": _iso(valid_until),
        "graph": graph,
    }
