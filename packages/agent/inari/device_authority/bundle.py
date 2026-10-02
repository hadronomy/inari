from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .authority import canonical_digest, verify_signed
from .models import (
    AuthorityScope,
    DeviceTestResult,
    OutputEvidence,
    SignedAuthorityRevision,
    SignedBindingRevision,
    SignedDeviceTestEvidence,
    SignedDriverProfile,
    SignedHardwareCertificationMatrixRow,
    SignerPurpose,
    SignerRecord,
)

Identifier = Annotated[str, Field(min_length=1, max_length=256)]


class BundleModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        val_json_bytes="hex",
        ser_json_bytes="hex",
    )


class BindingActivation(BundleModel):
    revision_id: Identifier
    evidence_id: Identifier


class AuthorityTrust(BundleModel):
    """Administrator-provisioned Controller key and permitted Odoo scope."""

    scope: AuthorityScope
    signer: SignerRecord


class AuthorityManifest(BundleModel):
    """A complete activation set for one Agent and one exact Odoo scope."""

    contract: Literal["inari.device-authority.v1"]
    agent_id: Identifier
    scope: AuthorityScope
    signers: Annotated[tuple[SignerRecord, ...], Field(max_length=128)]
    profiles: Annotated[tuple[SignedDriverProfile, ...], Field(max_length=256)]
    certification_rows: Annotated[
        tuple[SignedHardwareCertificationMatrixRow, ...], Field(max_length=1024)
    ]
    bindings: Annotated[tuple[SignedBindingRevision, ...], Field(max_length=1024)]
    evidence: Annotated[tuple[SignedDeviceTestEvidence, ...], Field(max_length=1024)]
    activations: Annotated[tuple[BindingActivation, ...], Field(max_length=1024)]


class AuthorityBundle(BundleModel):
    revision: SignedAuthorityRevision
    manifest: AuthorityManifest

    def verify(
        self,
        *,
        trusted_signer: SignerRecord,
        agent_id: str,
        scope: AuthorityScope,
        now: datetime,
    ) -> None:
        """Check the signed manifest before any projection reaches storage."""
        manifest = self.manifest
        if manifest.agent_id != agent_id or manifest.scope != scope:
            raise ValueError("The authority bundle targets another Agent or scope.")
        signed = self.revision
        if signed.signer_key_id != trusted_signer.key_id:
            raise ValueError("The authority revision signing key is not trusted.")
        verify_signed(
            payload=signed.revision,
            digest=signed.digest,
            signer=trusted_signer,
            signature=signed.signature,
            purpose=SignerPurpose.AUTHORITY_REVISION,
            now=now,
        )
        if (
            canonical_digest(manifest.model_dump(mode="json"))
            != signed.revision.manifest_digest
        ):
            raise ValueError("The authority manifest digest differs.")
        if (
            signed.revision.effective_at > now
            or signed.revision.expires_at is None
            or signed.revision.expires_at <= now
        ):
            raise ValueError("The authority bundle needs a current, bounded lifetime.")
        signers = {item.key_id: item for item in manifest.signers}
        if len(signers) != len(manifest.signers):
            raise ValueError("The authority manifest repeats a signing key.")
        if any(
            item.key_id == trusted_signer.key_id
            or item.purpose is SignerPurpose.AUTHORITY_REVISION
            for item in manifest.signers
        ):
            raise ValueError("The manifest cannot replace the authority trust key.")
        groups = (
            (manifest.profiles, "profile", SignerPurpose.DRIVER_PROFILE),
            (manifest.certification_rows, "row", SignerPurpose.CERTIFICATION_MATRIX),
            (manifest.bindings, "revision", SignerPurpose.BINDING_REVISION),
            (manifest.evidence, "evidence", SignerPurpose.DEVICE_TEST_EVIDENCE),
        )
        for records, attribute, purpose in groups:
            for record in records:
                verify_signed(
                    payload=getattr(record, attribute),
                    digest=record.digest,
                    signer=signers.get(record.signer_key_id),
                    signature=record.signature,
                    purpose=purpose,
                    now=now,
                )
        self._verify_graphs(now)

    def _verify_graphs(self, now: datetime) -> None:
        manifest = self.manifest
        profiles = {item.digest: item.profile for item in manifest.profiles}
        rows = {item.row.row_id: item.row for item in manifest.certification_rows}
        bindings = {
            item.revision.revision_id: item.revision for item in manifest.bindings
        }
        evidence = {
            item.evidence.evidence_id: item.evidence for item in manifest.evidence
        }
        for indexed, records in (
            (profiles, manifest.profiles),
            (rows, manifest.certification_rows),
            (bindings, manifest.bindings),
            (evidence, manifest.evidence),
        ):
            if len(indexed) != len(records):
                raise ValueError("The authority manifest repeats a record.")
        if any(item.scope != manifest.scope for item in bindings.values()):
            raise ValueError("A Binding Revision belongs to another scope.")
        purposes: set[str] = set()
        for activation in manifest.activations:
            binding = bindings.get(activation.revision_id)
            test = evidence.get(activation.evidence_id)
            if binding is None or test is None:
                raise ValueError("An activation refers to an absent record.")
            profile = profiles.get(binding.driver_profile_digest)
            row = rows.get(binding.matrix_row_id)
            if profile is None or row is None:
                raise ValueError("An activation needs its complete Driver graph.")
            if binding.purpose in purposes:
                raise ValueError(
                    "A scope cannot activate two bindings for one purpose."
                )
            purposes.add(binding.purpose)
            capability = next(
                (
                    item
                    for item in profile.capabilities
                    if item.capability_id == binding.capability_id
                ),
                None,
            )
            if (
                capability is None
                or capability.options_digest != binding.options_digest
                or row.driver_id != profile.driver_id
                or test.revision_id != binding.revision_id
                or test.result is not DeviceTestResult.PASSED
            ):
                raise ValueError("The active Binding Revision graph differs.")
            for name in (
                "device_id",
                "device_identity_digest",
                "capability_id",
                "driver_profile_digest",
            ):
                if getattr(binding, name) != getattr(row, name) or getattr(
                    binding, name
                ) != getattr(test, name):
                    raise ValueError("The active Device certification graph differs.")
            if test.matrix_row_id != row.row_id:
                raise ValueError("The Device Test refers to another certification row.")
            evidence_levels = (
                OutputEvidence.TRANSPORT,
                OutputEvidence.SPOOLER,
                OutputEvidence.DEVICE,
            )
            if evidence_levels.index(test.output_evidence) < evidence_levels.index(
                capability.output_evidence
            ):
                raise ValueError(
                    "The Device Test evidence is weaker than the capability contract."
                )
            for start, end in (
                (profile.effective_at, profile.expires_at),
                (row.effective_at, row.expires_at),
                (test.tested_at, test.valid_until),
            ):
                if start > now or (end is not None and end <= now):
                    raise ValueError("An active authority record is not current.")
