use std::collections::{BTreeMap, BTreeSet};

use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::canonical::{invalid, replace_times, sealed, validity};
use super::{
    AuthorityDigest, AuthorityTime, CanonicalRecord, ControllerRecordPayload, HexBytes, Identifier,
    PositiveInteger, RecordPayload,
};
use crate::GatewayResult;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScopeKind {
    Site,
    PosConfiguration,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorityScope {
    pub database: Identifier,
    pub organization_id: Identifier,
    pub site_id: Identifier,
    pub kind: ScopeKind,
    #[serde(deserialize_with = "Option::deserialize")]
    pub pos_configuration_id: Option<Identifier>,
}

impl AuthorityScope {
    pub fn validate(&self) -> GatewayResult<()> {
        if (self.kind == ScopeKind::PosConfiguration) != self.pos_configuration_id.is_some() {
            return Err(invalid("only POS scopes contain a POS configuration"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputEvidence {
    Transport,
    Spooler,
    Device,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceTestResult {
    Passed,
    FailedEnvironment,
    FailedContract,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SignerPurpose {
    AuthorityRevision,
    DriverProfile,
    CertificationMatrix,
    BindingRevision,
    DeviceTestEvidence,
    DeviceObservation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SignerState {
    Active,
    Retired,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignerRecord {
    pub key_id: Identifier,
    pub purpose: SignerPurpose,
    pub public_key: HexBytes<32>,
    pub state: SignerState,
    pub not_before: AuthorityTime,
    #[serde(deserialize_with = "Option::deserialize")]
    pub not_after: Option<AuthorityTime>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub retired_at: Option<AuthorityTime>,
}

impl SignerRecord {
    pub fn validate(&self) -> GatewayResult<()> {
        validity(self.not_before, self.not_after)?;
        if (self.state == SignerState::Active) != self.retired_at.is_none()
            || self
                .retired_at
                .is_some_and(|at| at < self.not_before)
        {
            return Err(invalid("signer state and retirement time disagree"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorityRevision {
    pub revision_id: Identifier,
    pub revision_number: PositiveInteger,
    pub manifest_digest: AuthorityDigest,
    pub effective_at: AuthorityTime,
    #[serde(deserialize_with = "Option::deserialize")]
    pub expires_at: Option<AuthorityTime>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceCapability {
    pub capability_id: Identifier,
    pub operation: Identifier,
    pub media_type: Identifier,
    pub contract_major: PositiveInteger,
    pub output_evidence: OutputEvidence,
    pub max_payload_bytes: PositiveInteger,
    pub max_copies: PositiveInteger,
    pub options_digest: AuthorityDigest,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DriverProfile {
    pub profile_id: Identifier,
    pub version: Identifier,
    pub driver_id: Identifier,
    pub min_agent_version: Identifier,
    pub capabilities: Vec<DeviceCapability>,
    pub effective_at: AuthorityTime,
    #[serde(deserialize_with = "Option::deserialize")]
    pub expires_at: Option<AuthorityTime>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareCertificationMatrixRow {
    pub row_id: Identifier,
    pub version: PositiveInteger,
    pub device_id: Identifier,
    pub device_identity_digest: AuthorityDigest,
    pub manufacturer: Identifier,
    pub model: Identifier,
    pub firmware_version: Identifier,
    pub firmware_build: Identifier,
    pub driver_id: Identifier,
    pub driver_profile_digest: AuthorityDigest,
    pub capability_id: Identifier,
    pub platform_backend_id: Identifier,
    pub connection: Identifier,
    pub media_profile: Identifier,
    pub operating_system: Identifier,
    pub release_set_id: Identifier,
    pub effective_at: AuthorityTime,
    #[serde(deserialize_with = "Option::deserialize")]
    pub expires_at: Option<AuthorityTime>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BindingRevision {
    pub binding_id: Identifier,
    pub revision_id: Identifier,
    pub revision_number: PositiveInteger,
    pub scope: AuthorityScope,
    pub purpose: Identifier,
    pub device_id: Identifier,
    pub device_identity_digest: AuthorityDigest,
    pub capability_id: Identifier,
    pub driver_profile_digest: AuthorityDigest,
    pub matrix_row_id: Identifier,
    pub options_digest: AuthorityDigest,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceTestEvidence {
    pub evidence_id: Identifier,
    pub revision_id: Identifier,
    pub device_id: Identifier,
    pub device_identity_digest: AuthorityDigest,
    pub capability_id: Identifier,
    pub driver_profile_digest: AuthorityDigest,
    pub matrix_row_id: Identifier,
    pub output_evidence: OutputEvidence,
    pub result: DeviceTestResult,
    pub test_pattern_digest: AuthorityDigest,
    pub tested_at: AuthorityTime,
    #[serde(deserialize_with = "Option::deserialize")]
    pub valid_until: Option<AuthorityTime>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceObservation {
    pub observation_id: Identifier,
    pub device_id: Identifier,
    pub device_identity_digest: AuthorityDigest,
    pub driver_id: Identifier,
    pub driver_profile_digest: AuthorityDigest,
    pub platform_backend_id: Identifier,
    pub connection: Identifier,
    pub media_profile: Identifier,
    pub firmware_version: Identifier,
    pub firmware_build: Identifier,
    pub operating_system: Identifier,
    pub ready: bool,
    pub state: Identifier,
    #[serde(deserialize_with = "Option::deserialize")]
    pub reason: Option<String>,
    pub observed_at: AuthorityTime,
}

macro_rules! record_payload {
    ($record:ident, $purpose:ident, $validate:expr, [$($time:ident),*]) => {
        impl sealed::Record for $record {}
        impl RecordPayload for $record {
            const PURPOSE: SignerPurpose = SignerPurpose::$purpose;
            fn validate(&self) -> GatewayResult<()> { ($validate)(self) }
            fn signing_value(&self) -> GatewayResult<Value> {
                replace_times(self, &[$((stringify!($time), self.$time.into())),*])
            }
        }
    };
}

record_payload!(
    AuthorityRevision,
    AuthorityRevision,
    |record: &AuthorityRevision| validity(record.effective_at, record.expires_at),
    [effective_at, expires_at]
);
record_payload!(
    DriverProfile,
    DriverProfile,
    |record: &DriverProfile| {
        validity(record.effective_at, record.expires_at)?;
        if record.capabilities.is_empty()
            || record
                .capabilities
                .iter()
                .map(|item| &item.capability_id)
                .collect::<BTreeSet<_>>()
                .len()
                != record.capabilities.len()
        {
            return Err(invalid("a Driver Profile needs at least one distinct capability"));
        }
        Ok(())
    },
    [effective_at, expires_at]
);
record_payload!(
    HardwareCertificationMatrixRow,
    CertificationMatrix,
    |record: &HardwareCertificationMatrixRow| validity(record.effective_at, record.expires_at),
    [effective_at, expires_at]
);
record_payload!(
    BindingRevision,
    BindingRevision,
    |record: &BindingRevision| record.scope.validate(),
    []
);
record_payload!(
    DeviceTestEvidence,
    DeviceTestEvidence,
    |record: &DeviceTestEvidence| validity(record.tested_at, record.valid_until),
    [tested_at, valid_until]
);
record_payload!(
    DeviceObservation,
    DeviceObservation,
    |_record: &DeviceObservation| Ok(()),
    [observed_at]
);

macro_rules! controller_record {
    ($($record:ident),*) => { $(
        impl sealed::Controller for $record {}
        impl ControllerRecordPayload for $record {}
    )* };
}
controller_record!(
    AuthorityRevision,
    DriverProfile,
    HardwareCertificationMatrixRow,
    BindingRevision
);

macro_rules! signed_record {
    ($signed:ident, $record:ident, $field:ident) => {
        #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
        #[serde(deny_unknown_fields)]
        pub struct $signed {
            pub $field: $record,
            pub digest: AuthorityDigest,
            pub signer_key_id: Identifier,
            pub signature: HexBytes<64>,
        }

        impl $signed {
            /// Attach a signature to validated bytes. Verify the envelope before accepting it.
            #[must_use]
            pub fn from_signature(
                canonical: CanonicalRecord<$record>,
                signer_key_id: Identifier,
                signature: HexBytes<64>,
            ) -> Self {
                Self {
                    $field: canonical.record,
                    digest: canonical.digest,
                    signer_key_id,
                    signature,
                }
            }

            pub fn verify(&self, signer: &SignerRecord, now: AuthorityTime) -> GatewayResult<()> {
                signer.validate()?;
                if signer.key_id != self.signer_key_id || signer.purpose != $record::PURPOSE {
                    return Err(invalid("the authority signer key or purpose differs"));
                }
                if signer.state != SignerState::Active
                    || now < signer.not_before
                    || signer
                        .not_after
                        .is_some_and(|end| now >= end)
                {
                    return Err(invalid("the authority signer is not current"));
                }
                let canonical = CanonicalRecord::new(self.$field.clone())?;
                if canonical.digest() != &self.digest {
                    return Err(invalid("the authority digest differs from its record"));
                }
                let key = VerifyingKey::from_bytes(signer.public_key.as_bytes())
                    .map_err(|_| invalid("the authority public key is invalid"))?;
                key.verify_strict(
                    canonical.as_bytes(),
                    &Signature::from_bytes(self.signature.as_bytes()),
                )
                .map_err(|_| invalid("the authority signature is invalid"))
            }
        }
    };
}
signed_record!(SignedAuthorityRevision, AuthorityRevision, revision);
signed_record!(SignedDriverProfile, DriverProfile, profile);
signed_record!(SignedHardwareCertificationMatrixRow, HardwareCertificationMatrixRow, row);
signed_record!(SignedBindingRevision, BindingRevision, revision);
signed_record!(SignedDeviceTestEvidence, DeviceTestEvidence, evidence);
signed_record!(SignedDeviceObservation, DeviceObservation, observation);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AuthorityContract {
    #[serde(rename = "inari.device-authority.v1")]
    V1,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BindingActivation {
    pub revision_id: Identifier,
    pub evidence_id: Identifier,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorityManifest {
    pub contract: AuthorityContract,
    pub agent_id: Identifier,
    pub scope: AuthorityScope,
    pub signers: Vec<SignerRecord>,
    pub profiles: Vec<SignedDriverProfile>,
    pub certification_rows: Vec<SignedHardwareCertificationMatrixRow>,
    pub bindings: Vec<SignedBindingRevision>,
    pub evidence: Vec<SignedDeviceTestEvidence>,
    pub activations: Vec<BindingActivation>,
}

impl AuthorityManifest {
    pub fn digest(&self) -> GatewayResult<AuthorityDigest> {
        self.validate()?;
        Ok(AuthorityDigest::of(&serde_json_canonicalizer::to_vec(self)?))
    }

    pub fn validate(&self) -> GatewayResult<()> {
        self.scope.validate()?;
        for signer in &self.signers {
            signer.validate()?;
        }
        for (length, limit) in [
            (self.signers.len(), 128),
            (self.profiles.len(), 256),
            (self.certification_rows.len(), 1024),
            (self.bindings.len(), 1024),
            (self.evidence.len(), 1024),
            (self.activations.len(), 1024),
        ] {
            if length > limit {
                return Err(invalid("the authority manifest exceeds its record limit"));
            }
        }
        if self
            .signers
            .iter()
            .map(|item| &item.key_id)
            .collect::<BTreeSet<_>>()
            .len()
            != self.signers.len()
            || self
                .profiles
                .iter()
                .map(|item| &item.digest)
                .collect::<BTreeSet<_>>()
                .len()
                != self.profiles.len()
            || self
                .certification_rows
                .iter()
                .map(|item| &item.row.row_id)
                .collect::<BTreeSet<_>>()
                .len()
                != self.certification_rows.len()
            || self
                .bindings
                .iter()
                .map(|item| &item.revision.revision_id)
                .collect::<BTreeSet<_>>()
                .len()
                != self.bindings.len()
            || self
                .evidence
                .iter()
                .map(|item| &item.evidence.evidence_id)
                .collect::<BTreeSet<_>>()
                .len()
                != self.evidence.len()
        {
            return Err(invalid("the authority manifest repeats a record"));
        }
        if self
            .bindings
            .iter()
            .any(|item| item.revision.scope != self.scope)
        {
            return Err(invalid("a Binding Revision belongs to another scope"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorityBundle {
    pub revision: SignedAuthorityRevision,
    pub manifest: AuthorityManifest,
}

impl AuthorityBundle {
    /// Verify the independently trusted root, exact target, record signatures, and activation graph.
    ///
    /// The manifest cannot supply or replace its own authority trust key.
    pub fn verify(
        &self,
        trusted_signer: &SignerRecord,
        agent_id: &str,
        scope: &AuthorityScope,
        now: AuthorityTime,
    ) -> GatewayResult<()> {
        if self.manifest.agent_id.as_str() != agent_id || &self.manifest.scope != scope {
            return Err(invalid("the authority bundle targets another Agent or scope"));
        }
        self.revision
            .verify(trusted_signer, now)?;
        let revision = &self.revision.revision;
        if revision.effective_at > now
            || revision
                .expires_at
                .is_none_or(|end| end <= now)
        {
            return Err(invalid("an authority bundle needs a current, bounded lifetime"));
        }
        if self.manifest.digest()? != revision.manifest_digest {
            return Err(invalid("the authority manifest digest differs"));
        }
        let signers: BTreeMap<_, _> = self
            .manifest
            .signers
            .iter()
            .map(|item| (&item.key_id, item))
            .collect();
        if self
            .manifest
            .signers
            .iter()
            .any(|item| {
                item.key_id == trusted_signer.key_id
                    || item.purpose == SignerPurpose::AuthorityRevision
            })
        {
            return Err(invalid("the manifest cannot replace the authority trust key"));
        }
        let signer = |key: &Identifier| {
            signers
                .get(key)
                .copied()
                .ok_or_else(|| invalid("the authority record signing key is absent"))
        };
        for item in &self.manifest.profiles {
            item.verify(signer(&item.signer_key_id)?, now)?;
        }
        for item in &self.manifest.certification_rows {
            item.verify(signer(&item.signer_key_id)?, now)?;
        }
        for item in &self.manifest.bindings {
            item.verify(signer(&item.signer_key_id)?, now)?;
        }
        for item in &self.manifest.evidence {
            item.verify(signer(&item.signer_key_id)?, now)?;
        }
        self.verify_activations(now)
    }

    fn verify_activations(&self, now: AuthorityTime) -> GatewayResult<()> {
        let mut purposes = BTreeSet::new();
        for activation in &self.manifest.activations {
            let binding = self
                .manifest
                .bindings
                .iter()
                .find(|item| item.revision.revision_id == activation.revision_id)
                .map(|item| &item.revision)
                .ok_or_else(|| invalid("an activation Binding Revision is absent"))?;
            let evidence = self
                .manifest
                .evidence
                .iter()
                .find(|item| item.evidence.evidence_id == activation.evidence_id)
                .map(|item| &item.evidence)
                .ok_or_else(|| invalid("activation evidence is absent"))?;
            let profile = self
                .manifest
                .profiles
                .iter()
                .find(|item| item.digest == binding.driver_profile_digest)
                .map(|item| &item.profile)
                .ok_or_else(|| invalid("the active Driver Profile is absent"))?;
            let row = self
                .manifest
                .certification_rows
                .iter()
                .find(|item| item.row.row_id == binding.matrix_row_id)
                .map(|item| &item.row)
                .ok_or_else(|| invalid("the active certification row is absent"))?;
            let capability = profile
                .capabilities
                .iter()
                .find(|item| item.capability_id == binding.capability_id)
                .ok_or_else(|| invalid("the active Device Capability is absent"))?;
            if !purposes.insert(&binding.purpose)
                || capability.options_digest != binding.options_digest
                || row.driver_id != profile.driver_id
                || evidence.revision_id != binding.revision_id
                || evidence.result != DeviceTestResult::Passed
                || binding.device_id != row.device_id
                || binding.device_id != evidence.device_id
                || binding.device_identity_digest != row.device_identity_digest
                || binding.device_identity_digest != evidence.device_identity_digest
                || binding.capability_id != row.capability_id
                || binding.capability_id != evidence.capability_id
                || binding.driver_profile_digest != row.driver_profile_digest
                || binding.driver_profile_digest != evidence.driver_profile_digest
                || evidence.matrix_row_id != row.row_id
                || evidence.output_evidence < capability.output_evidence
            {
                return Err(invalid("the active Binding Revision graph differs"));
            }
            for (start, end) in [
                (profile.effective_at, profile.expires_at),
                (row.effective_at, row.expires_at),
                (evidence.tested_at, evidence.valid_until),
            ] {
                if start > now || end.is_some_and(|end| end <= now) {
                    return Err(invalid("an active authority record is not current"));
                }
            }
        }
        Ok(())
    }
}
