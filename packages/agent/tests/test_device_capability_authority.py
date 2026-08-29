from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from inari.device_authority import (
    CapabilityAdmissionTarget,
    AuthorityRevision,
    AuthorityState,
    AuthorityStatus,
    AuthorityError,
    AuthorityErrorCode,
    AuthorityScope,
    BindingRevision,
    DeviceCapability,
    DeviceCapabilityAuthority,
    DeviceObservation,
    DeviceTestEvidence,
    DeviceTestResult,
    DriverProfile,
    HardwareCertificationMatrixRow,
    OutputEvidence,
    RevocationSubjectKind,
    ScopeKind,
    SignedBindingRevision,
    SignedAuthorityRevision,
    SignerPurpose,
    SignerRecord,
    SignerState,
    SignedDeviceObservation,
    SignedDeviceTestEvidence,
    SignedDriverProfile,
    SignedHardwareCertificationMatrixRow,
    canonical_digest,
    canonical_json_bytes,
)


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
OPTIONS_DIGEST = "1" * 64
DEVICE_IDENTITY_DIGEST = "2" * 64
TEST_PATTERN_DIGEST = "3" * 64


@dataclass
class ProjectionStore:
    authority_state: AuthorityState
    binding: SignedBindingRevision
    active_evidence_id: str
    profile: SignedDriverProfile
    row: SignedHardwareCertificationMatrixRow
    evidence: SignedDeviceTestEvidence
    signers: dict[str, SignerRecord]
    private_keys: dict[str, Ed25519PrivateKey]
    revoked: set[tuple[RevocationSubjectKind, str, str]]

    def read_authority_state(self) -> AuthorityState:
        return self.authority_state

    def read_active_test_evidence_id(self, revision_id: str) -> str | None:
        if revision_id != self.binding.revision.revision_id:
            return None
        return self.active_evidence_id

    def read_binding_revision(self, revision_id: str) -> SignedBindingRevision | None:
        return (
            self.binding if revision_id == self.binding.revision.revision_id else None
        )

    def read_driver_profile(self, digest: str) -> SignedDriverProfile | None:
        return self.profile if digest == self.profile.digest else None

    def read_certification_row(
        self, row_id: str
    ) -> SignedHardwareCertificationMatrixRow | None:
        return self.row if row_id == self.row.row.row_id else None

    def read_device_test(self, evidence_id: str) -> SignedDeviceTestEvidence | None:
        return (
            self.evidence if evidence_id == self.evidence.evidence.evidence_id else None
        )

    def read_signer(self, key_id: str, purpose: SignerPurpose) -> SignerRecord | None:
        return self.signers.get(key_id)

    def is_revoked(
        self,
        subject_kind: RevocationSubjectKind,
        subject_id: str,
        subject_digest: str,
    ) -> bool:
        return (subject_kind, subject_id, subject_digest) in self.revoked


@dataclass
class ObservationStore:
    current: SignedDeviceObservation
    private_key: Ed25519PrivateKey

    def read_current(self, device_id: str) -> SignedDeviceObservation | None:
        return self.current if self.current.observation.device_id == device_id else None


def _key(purpose: SignerPurpose, name: str) -> tuple[SignerRecord, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return SignerRecord(
        key_id=name,
        purpose=purpose,
        public_key=public,
        state=SignerState.ACTIVE,
        not_before=NOW - timedelta(days=1),
        not_after=NOW + timedelta(days=90),
        retired_at=None,
    ), private


def _signed(
    value: object,
    private: Ed25519PrivateKey,
    signer_key_id: str,
):
    digest = canonical_digest(value)
    return digest, signer_key_id, private.sign(canonical_json_bytes(value))


def _fixture() -> tuple[
    ProjectionStore,
    ObservationStore,
    CapabilityAdmissionTarget,
    datetime,
]:
    revision_value = AuthorityRevision(
        revision_id="authority-revision-1",
        revision_number=1,
        manifest_digest="4" * 64,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=7),
    )
    revision_signer, revision_private = _key(
        SignerPurpose.AUTHORITY_REVISION,
        "authority-revision-key",
    )
    revision_digest, _, revision_signature = _signed(
        revision_value,
        revision_private,
        revision_signer.key_id,
    )
    authority_state = AuthorityState(
        status=AuthorityStatus.READY,
        current_revision=SignedAuthorityRevision(
            revision=revision_value,
            digest=revision_digest,
            signer_key_id=revision_signer.key_id,
            signature=revision_signature,
        ),
    )
    scope = AuthorityScope(
        database="odoo-prod",
        organization_id="org-1",
        site_id="site-1",
        kind=ScopeKind.POS_CONFIGURATION,
        pos_configuration_id="pos-1",
    )
    capability = DeviceCapability(
        capability_id="receipt-image-v1",
        operation="receipt_image",
        media_type="image/jpeg",
        contract_major=1,
        output_evidence=OutputEvidence.TRANSPORT,
        max_payload_bytes=2 * 1024 * 1024,
        max_copies=1,
        options_digest=OPTIONS_DIGEST,
    )
    profile_value = DriverProfile(
        profile_id="profile-1",
        version="1.0.0",
        driver_id="driver-escpos",
        min_agent_version="1.20.0",
        capabilities=(capability,),
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
    )
    profile_signer, profile_private = _key(SignerPurpose.DRIVER_PROFILE, "profile-key")
    profile_digest, _, profile_signature = _signed(
        profile_value, profile_private, profile_signer.key_id
    )
    profile = SignedDriverProfile(
        profile_value,
        profile_digest,
        profile_signer.key_id,
        profile_signature,
    )
    row_value = HardwareCertificationMatrixRow(
        row_id="matrix-1",
        version=1,
        device_id="device-1",
        device_identity_digest=DEVICE_IDENTITY_DIGEST,
        manufacturer="Acme",
        model="Receipt 2000",
        firmware_version="2.1",
        firmware_build="2026.08",
        driver_id=profile_value.driver_id,
        driver_profile_digest=profile_digest,
        capability_id=capability.capability_id,
        platform_backend_id="cups",
        connection="usb",
        media_profile="receipt-80mm",
        operating_system="linux-x86_64",
        release_set_id="release-2026-08",
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
    )
    matrix_signer, matrix_private = _key(
        SignerPurpose.CERTIFICATION_MATRIX, "matrix-key"
    )
    row_digest, _, row_signature = _signed(
        row_value, matrix_private, matrix_signer.key_id
    )
    row = SignedHardwareCertificationMatrixRow(
        row_value,
        row_digest,
        matrix_signer.key_id,
        row_signature,
    )
    binding = BindingRevision(
        binding_id="binding-1",
        revision_id="revision-1",
        revision_number=1,
        scope=scope,
        purpose="pos_receipt",
        device_id=row_value.device_id,
        device_identity_digest=row_value.device_identity_digest,
        capability_id=capability.capability_id,
        driver_profile_digest=profile_digest,
        matrix_row_id=row_value.row_id,
        options_digest=OPTIONS_DIGEST,
    )
    binding_signer, binding_private = _key(
        SignerPurpose.BINDING_REVISION,
        "binding-key",
    )
    binding_digest, _, binding_signature = _signed(
        binding,
        binding_private,
        binding_signer.key_id,
    )
    signed_binding = SignedBindingRevision(
        revision=binding,
        digest=binding_digest,
        signer_key_id=binding_signer.key_id,
        signature=binding_signature,
    )
    evidence_value = DeviceTestEvidence(
        evidence_id="test-1",
        revision_id=binding.revision_id,
        device_id=binding.device_id,
        device_identity_digest=binding.device_identity_digest,
        capability_id=binding.capability_id,
        driver_profile_digest=binding.driver_profile_digest,
        matrix_row_id=binding.matrix_row_id,
        output_evidence=OutputEvidence.TRANSPORT,
        result=DeviceTestResult.PASSED,
        test_pattern_digest=TEST_PATTERN_DIGEST,
        tested_at=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=1),
    )
    evidence_signer, evidence_private = _key(
        SignerPurpose.DEVICE_TEST_EVIDENCE, "test-key"
    )
    evidence_digest, _, evidence_signature = _signed(
        evidence_value, evidence_private, evidence_signer.key_id
    )
    evidence = SignedDeviceTestEvidence(
        evidence_value,
        evidence_digest,
        evidence_signer.key_id,
        evidence_signature,
    )
    observation_value = DeviceObservation(
        observation_id="observation-1",
        device_id=row_value.device_id,
        device_identity_digest=row_value.device_identity_digest,
        driver_id=row_value.driver_id,
        driver_profile_digest=row_value.driver_profile_digest,
        platform_backend_id=row_value.platform_backend_id,
        connection=row_value.connection,
        media_profile=row_value.media_profile,
        firmware_version=row_value.firmware_version,
        firmware_build=row_value.firmware_build,
        operating_system=row_value.operating_system,
        ready=True,
        state="ready",
        reason=None,
        observed_at=NOW,
    )
    observation_signer, observation_private = _key(
        SignerPurpose.DEVICE_OBSERVATION, "observation-key"
    )
    observation_digest, _, observation_signature = _signed(
        observation_value, observation_private, observation_signer.key_id
    )
    observation = SignedDeviceObservation(
        observation_value,
        observation_digest,
        observation_signer.key_id,
        observation_signature,
    )
    projections = ProjectionStore(
        authority_state,
        signed_binding,
        "test-1",
        profile,
        row,
        evidence,
        {
            revision_signer.key_id: revision_signer,
            profile_signer.key_id: profile_signer,
            matrix_signer.key_id: matrix_signer,
            binding_signer.key_id: binding_signer,
            evidence_signer.key_id: evidence_signer,
            observation_signer.key_id: observation_signer,
        },
        {
            revision_signer.key_id: revision_private,
            profile_signer.key_id: profile_private,
            matrix_signer.key_id: matrix_private,
            binding_signer.key_id: binding_private,
            evidence_signer.key_id: evidence_private,
            observation_signer.key_id: observation_private,
        },
        set(),
    )
    request = CapabilityAdmissionTarget(
        scope=scope,
        purpose=binding.purpose,
        device_id=binding.device_id,
        binding_revision_id=binding.revision_id,
        operation=capability.operation,
        media_type=capability.media_type,
        contract_major=capability.contract_major,
        options_digest=OPTIONS_DIGEST,
    )
    return (
        projections,
        ObservationStore(observation, observation_private),
        request,
        observation_digest,
    )


def _authority(
    projections: ProjectionStore,
    observations: ObservationStore,
    *,
    current_agent_version: str = "1.20.0",
) -> DeviceCapabilityAuthority:
    return DeviceCapabilityAuthority(
        projections=projections,
        observations=observations,
        current_agent_version=current_agent_version,
        permit_ttl=timedelta(seconds=30),
        observation_max_age=timedelta(seconds=5),
    )


def test_authorize_compiles_an_opaque_permit_and_pre_io_check_passes() -> None:
    projections, observations, request, _ = _fixture()
    authority = _authority(projections, observations)

    permit = authority.authorize(request, now=NOW)
    stamp = authority.check(permit, now=NOW + timedelta(seconds=1))

    assert stamp.binding_revision_id == request.binding_revision_id
    assert stamp.authority_revision_id == "authority-revision-1"
    assert stamp.graph_digest == permit.authority_proof.graph_digest
    assert stamp.binding_revision_digest == projections.binding.digest
    assert stamp.driver_profile_digest == projections.profile.digest
    assert stamp.matrix_row_digest == projections.row.digest
    assert stamp.test_evidence_digest == projections.evidence.digest
    assert stamp.device_id == request.device_id
    assert repr(permit) == "AdmissionPermit(<opaque>)"
    with pytest.raises(AttributeError):
        permit.authority_proof = stamp  # type: ignore[misc]


def test_permit_from_another_authority_instance_is_rejected() -> None:
    projections, observations, request, _ = _fixture()
    permit = _authority(projections, observations).authorize(request, now=NOW)

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).check(permit, now=NOW)

    assert error.value.code is AuthorityErrorCode.INVALID_REQUEST


def test_quarantined_authority_cannot_authorize_device_work() -> None:
    projections, observations, request, _ = _fixture()
    projections.authority_state = replace(
        projections.authority_state,
        status=AuthorityStatus.QUARANTINED,
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.AUTHORITY_UNAVAILABLE


def test_profile_payload_tampering_fails_digest_check() -> None:
    projections, _, request, _ = _fixture()
    projections.profile = replace(
        projections.profile,
        profile=replace(projections.profile.profile, version="9.9.9"),
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, _fixture()[1]).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.SIGNATURE_INVALID


def test_binding_revision_tampering_fails_signature_check() -> None:
    projections, observations, request, _ = _fixture()
    projections.binding = replace(
        projections.binding,
        revision=replace(projections.binding.revision, purpose="stock_label"),
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.SIGNATURE_INVALID


def test_profile_that_requires_a_newer_agent_is_rejected() -> None:
    projections, observations, request, _ = _fixture()

    with pytest.raises(AuthorityError) as error:
        _authority(
            projections,
            observations,
            current_agent_version="1.19.9",
        ).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.GRAPH_MISMATCH


def test_signer_from_the_wrong_purpose_is_rejected_before_verification() -> None:
    projections, observations, request, _ = _fixture()
    signer = projections.signers[projections.profile.signer_key_id]
    projections.signers[signer.key_id] = replace(
        signer, purpose=SignerPurpose.CERTIFICATION_MATRIX
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.SIGNER_PURPOSE_MISMATCH


def test_revoked_profile_is_rejected() -> None:
    projections, observations, request, _ = _fixture()
    projections.revoked.add(
        (
            RevocationSubjectKind.DRIVER_PROFILE,
            projections.profile.profile.profile_id,
            projections.profile.digest,
        )
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.REVOKED


def test_failed_device_test_cannot_authorize_device_work() -> None:
    projections, observations, request, _ = _fixture()
    failed = replace(
        projections.evidence.evidence,
        result=DeviceTestResult.FAILED_CONTRACT,
    )
    digest, _, signature = _signed(
        failed,
        projections.private_keys[projections.evidence.signer_key_id],
        projections.evidence.signer_key_id,
    )
    projections.evidence = replace(
        projections.evidence,
        evidence=failed,
        digest=digest,
        signature=signature,
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.TEST_REQUIRED


def test_inactive_device_test_cannot_authorize_device_work() -> None:
    projections, observations, request, _ = _fixture()
    projections.active_evidence_id = "another-test"

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.TEST_REQUIRED


def test_signer_revoked_after_admission_blocks_device_io() -> None:
    projections, observations, request, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize(request, now=NOW)
    signer = projections.signers[projections.profile.signer_key_id]
    projections.signers[signer.key_id] = replace(
        signer,
        state=SignerState.RETIRED,
        retired_at=NOW + timedelta(milliseconds=500),
    )

    with pytest.raises(AuthorityError) as error:
        authority.check(permit, now=NOW + timedelta(seconds=1))

    assert error.value.code is AuthorityErrorCode.REVOKED


def test_signer_outside_its_validity_window_is_rejected() -> None:
    projections, observations, request, _ = _fixture()
    signer = projections.signers[projections.profile.signer_key_id]
    projections.signers[signer.key_id] = replace(
        signer,
        not_before=NOW + timedelta(seconds=1),
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.REVOKED


def test_scope_mismatch_is_rejected() -> None:
    projections, observations, request, _ = _fixture()
    request = replace(
        request,
        scope=replace(request.scope, site_id="other-site"),
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.SCOPE_MISMATCH


def test_graph_mismatch_is_rejected() -> None:
    projections, observations, request, _ = _fixture()
    request = replace(request, operation="other-operation")

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.GRAPH_MISMATCH


def test_matrix_row_for_another_capability_is_rejected() -> None:
    projections, observations, request, _ = _fixture()
    changed = replace(projections.row.row, capability_id="label-document-v1")
    digest, _, signature = _signed(
        changed,
        projections.private_keys[projections.row.signer_key_id],
        projections.row.signer_key_id,
    )
    projections.row = replace(
        projections.row,
        row=changed,
        digest=digest,
        signature=signature,
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.GRAPH_MISMATCH


def test_requested_expiry_and_permit_expiry_are_enforced() -> None:
    projections, observations, request, _ = _fixture()
    expired = replace(request, requested_expires_at=NOW)
    authority = _authority(projections, observations)

    with pytest.raises(AuthorityError) as error:
        authority.authorize(expired, now=NOW)
    assert error.value.code is AuthorityErrorCode.EXPIRED

    permit = authority.authorize(request, now=NOW)
    with pytest.raises(AuthorityError) as error:
        authority.check(permit, now=NOW + timedelta(seconds=30))
    assert error.value.code is AuthorityErrorCode.EXPIRED


def test_durable_proof_outlives_the_in_memory_permit_lease() -> None:
    projections, observations, request, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize(request, now=NOW)
    proof = permit.authority_proof
    refreshed = replace(
        observations.current.observation,
        observation_id="observation-later",
        observed_at=NOW + timedelta(minutes=1),
    )
    digest, _, signature = _signed(
        refreshed,
        observations.private_key,
        observations.current.signer_key_id,
    )
    observations.current = replace(
        observations.current,
        observation=refreshed,
        digest=digest,
        signature=signature,
    )

    checked = authority.check_proof(proof, now=NOW + timedelta(minutes=1))

    assert proof.valid_until == NOW + timedelta(days=1)
    assert checked == proof


def test_observation_drift_fails_before_admission() -> None:
    projections, observations, request, _ = _fixture()
    changed = replace(
        observations.current.observation,
        firmware_build="different-build",
    )
    digest, _, signature = _signed(
        changed,
        observations.private_key,
        observations.current.signer_key_id,
    )
    observations.current = replace(
        observations.current,
        observation=changed,
        digest=digest,
        signature=signature,
    )

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).authorize(request, now=NOW)

    assert error.value.code is AuthorityErrorCode.OBSERVATION_DRIFT


def test_observation_drift_fails_at_the_pre_io_seam() -> None:
    projections, observations, request, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize(request, now=NOW)
    changed = replace(
        observations.current.observation,
        firmware_version="different-firmware",
    )
    digest, _, signature = _signed(
        changed,
        observations.private_key,
        observations.current.signer_key_id,
    )
    observations.current = replace(
        observations.current,
        observation=changed,
        digest=digest,
        signature=signature,
    )

    with pytest.raises(AuthorityError) as error:
        authority.check(permit, now=NOW + timedelta(seconds=1))

    assert error.value.code is AuthorityErrorCode.OBSERVATION_DRIFT


def test_refreshed_matching_observation_remains_authorized_before_device_io() -> None:
    projections, observations, request, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize(request, now=NOW)
    refreshed = replace(
        observations.current.observation,
        observation_id="observation-2",
        observed_at=NOW + timedelta(seconds=1),
    )
    digest, _, signature = _signed(
        refreshed,
        observations.private_key,
        observations.current.signer_key_id,
    )
    observations.current = replace(
        observations.current,
        observation=refreshed,
        digest=digest,
        signature=signature,
    )

    proof = authority.check(permit, now=NOW + timedelta(seconds=1))

    assert proof == permit.authority_proof


def test_canonical_digest_is_order_independent_for_object_keys() -> None:
    first = {"z": 1, "a": "value"}
    second = {"a": "value", "z": 1}

    assert canonical_digest(first) == canonical_digest(second)


def test_canonical_digest_rejects_non_string_object_keys() -> None:
    with pytest.raises(TypeError, match="string keys"):
        canonical_digest({1: "numeric", "1": "text"})


def test_authority_models_reject_untyped_enums_and_boolean_integers() -> None:
    with pytest.raises(ValueError, match="kind"):
        AuthorityScope(
            database="odoo",
            organization_id="org-1",
            site_id="site-1",
            kind="site",  # type: ignore[arg-type]
            pos_configuration_id=None,
        )

    with pytest.raises(ValueError, match="max_payload_bytes"):
        DeviceCapability(
            capability_id="receipt-image-v1",
            operation="receipt_image",
            media_type="image/jpeg",
            contract_major=1,
            output_evidence=OutputEvidence.TRANSPORT,
            max_payload_bytes=True,  # type: ignore[arg-type]
            max_copies=1,
            options_digest=OPTIONS_DIGEST,
        )
