from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from inari.device_authority import (
    AuthorityError,
    AuthorityErrorCode,
    AuthorityStatus,
    DeviceTestPermit,
    DeviceCapabilityAuthority,
    RevocationSubjectKind,
)

from .test_device_capability_authority import NOW, _authority, _fixture, _signed


def test_first_device_test_does_not_require_activation_or_previous_evidence():
    projections, observations, target, _ = _fixture()
    projections.active_evidence_id = ""
    authority = _authority(projections, observations)

    with pytest.raises(AuthorityError) as error:
        authority.authorize(target, now=NOW)
    assert error.value.code is AuthorityErrorCode.TEST_REQUIRED

    permit = authority.authorize_test(target, now=NOW)
    checked = authority.check_test(permit, now=NOW + timedelta(seconds=1))

    assert isinstance(permit, DeviceTestPermit)
    assert checked.binding == projections.binding
    assert checked.certification == projections.row
    assert checked.expires_at == NOW + timedelta(seconds=30)


def test_device_test_permit_cannot_supply_business_admission_proof():
    projections, observations, target, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize_test(target, now=NOW)

    with pytest.raises(AuthorityError) as error:
        authority.check(permit, now=NOW)
    assert error.value.code is AuthorityErrorCode.INVALID_REQUEST
    with pytest.raises(AuthorityError) as error:
        authority.check_proof(permit.authorization, now=NOW)
    assert error.value.code is AuthorityErrorCode.INVALID_REQUEST
    assert not hasattr(permit, "authority_proof")
    with pytest.raises(AttributeError):
        permit.authorization = permit.authorization


def test_device_test_permit_is_bound_to_its_authority_instance():
    projections, observations, target, _ = _fixture()
    permit = _authority(projections, observations).authorize_test(target, now=NOW)

    with pytest.raises(AuthorityError) as error:
        _authority(projections, observations).check_test(permit, now=NOW)
    assert error.value.code is AuthorityErrorCode.INVALID_REQUEST


def test_device_test_deadline_cannot_exceed_thirty_seconds():
    projections, observations, target, _ = _fixture()
    authority = DeviceCapabilityAuthority(
        projections=projections,
        observations=observations,
        current_agent_version="1.20.0",
        permit_ttl=timedelta(minutes=2),
    )

    permit = authority.authorize_test(target, now=NOW)

    assert permit.authorization.expires_at == NOW + timedelta(seconds=30)


@pytest.mark.parametrize("seconds", [-1, 30])
def test_device_test_permit_is_not_valid_outside_its_interval(seconds):
    projections, observations, target, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize_test(target, now=NOW)

    with pytest.raises(AuthorityError) as error:
        authority.check_test(permit, now=NOW + timedelta(seconds=seconds))
    assert error.value.code is AuthorityErrorCode.EXPIRED


@pytest.mark.parametrize(
    "subject",
    [
        RevocationSubjectKind.AUTHORITY_REVISION,
        RevocationSubjectKind.BINDING_REVISION,
        RevocationSubjectKind.DRIVER_PROFILE,
        RevocationSubjectKind.CERTIFICATION_MATRIX_ROW,
    ],
)
def test_device_test_rechecks_revocations_before_io(subject):
    projections, observations, target, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize_test(target, now=NOW)
    record = {
        RevocationSubjectKind.AUTHORITY_REVISION: (
            projections.authority_state.current_revision.revision.revision_id,
            projections.authority_state.current_revision.digest,
        ),
        RevocationSubjectKind.BINDING_REVISION: (
            projections.binding.revision.revision_id,
            projections.binding.digest,
        ),
        RevocationSubjectKind.DRIVER_PROFILE: (
            projections.profile.profile.profile_id,
            projections.profile.digest,
        ),
        RevocationSubjectKind.CERTIFICATION_MATRIX_ROW: (
            projections.row.row.row_id,
            projections.row.digest,
        ),
    }[subject]
    projections.revoked.add((subject, *record))

    with pytest.raises(AuthorityError) as error:
        authority.check_test(permit, now=NOW + timedelta(seconds=1))
    assert error.value.code is AuthorityErrorCode.REVOKED


def test_device_test_rejects_another_scope_and_quarantine():
    projections, observations, target, _ = _fixture()
    authority = _authority(projections, observations)
    wrong_scope = replace(target, scope=replace(target.scope, site_id="another-site"))

    with pytest.raises(AuthorityError) as error:
        authority.authorize_test(wrong_scope, now=NOW)
    assert error.value.code is AuthorityErrorCode.SCOPE_MISMATCH

    permit = authority.authorize_test(target, now=NOW)
    projections.authority_state = replace(
        projections.authority_state, status=AuthorityStatus.QUARANTINED
    )
    with pytest.raises(AuthorityError) as error:
        authority.check_test(permit, now=NOW)
    assert error.value.code is AuthorityErrorCode.AUTHORITY_UNAVAILABLE


def test_device_test_rechecks_firmware_drift_before_io():
    projections, observations, target, _ = _fixture()
    authority = _authority(projections, observations)
    permit = authority.authorize_test(target, now=NOW)
    changed = replace(observations.current.observation, firmware_version="changed")
    digest, _, signature = _signed(
        changed, observations.private_key, observations.current.signer_key_id
    )
    observations.current = replace(
        observations.current, observation=changed, digest=digest, signature=signature
    )

    with pytest.raises(AuthorityError) as error:
        authority.check_test(permit, now=NOW)
    assert error.value.code is AuthorityErrorCode.OBSERVATION_DRIFT


@pytest.mark.parametrize("observed_firmware", ["unavailable", "changed"])
def test_unavailable_firmware_requires_an_exact_signed_matrix_match(observed_firmware):
    projections, observations, target, _ = _fixture()
    row = replace(
        projections.row.row,
        firmware_version="unavailable",
        firmware_build="unavailable",
    )
    signer_id = projections.row.signer_key_id
    digest, _, signature = _signed(row, projections.private_keys[signer_id], signer_id)
    projections.row = replace(
        projections.row, row=row, digest=digest, signature=signature
    )
    observation = replace(
        observations.current.observation,
        firmware_version=observed_firmware,
        firmware_build="unavailable",
    )
    digest, _, signature = _signed(
        observation, observations.private_key, observations.current.signer_key_id
    )
    observations.current = replace(
        observations.current,
        observation=observation,
        digest=digest,
        signature=signature,
    )
    authority = _authority(projections, observations)

    if observed_firmware == "unavailable":
        permit = authority.authorize_test(target, now=NOW)
        assert authority.check_test(permit, now=NOW).certification.row == row
        assert authority.check(authority.authorize(target, now=NOW), now=NOW)
    else:
        with pytest.raises(AuthorityError) as error:
            authority.authorize_test(target, now=NOW)
        assert error.value.code is AuthorityErrorCode.OBSERVATION_DRIFT
