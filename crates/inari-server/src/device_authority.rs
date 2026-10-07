//! Operator-approved bundle assembly with purpose-bound OpenBao signing.

use std::collections::BTreeSet;
use std::io::{Read, Write};
use std::num::NonZeroU32;
use std::path::Path;
use std::sync::Arc;

use chrono::{SubsecRound, Utc};
use inari_gateway::device_authority::{
    AuthorityBundle, AuthorityContract, AuthorityDigest, AuthorityManifest, AuthorityRevision,
    AuthorityScope, AuthorityTime, BindingActivation, BindingRevision, CanonicalRecord,
    DriverProfile, HardwareCertificationMatrixRow, HexBytes, Identifier, PositiveInteger,
    SignedAuthorityRevision, SignedBindingRevision, SignedDeviceTestEvidence, SignedDriverProfile,
    SignedHardwareCertificationMatrixRow, SignerPurpose, SignerRecord,
};
use serde::Deserialize;
use serde::de::DeserializeOwned;

use crate::config::{OpenBaoConfig, valid_openbao_name};
use crate::openbao::{AuthoritySigningKey, OpenBaoClient};
use crate::{AppError, AppResult};

const MAX_DOCUMENT_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TransitApproval {
    pub key_name: String,
    pub key_version: NonZeroU32,
    pub signer: SignerRecord,
}

/// The independent approval fixes the target and every Controller and Agent signing key.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorityApproval {
    pub agent_id: Identifier,
    pub scope: AuthorityScope,
    pub transit_mount: String,
    pub root: TransitApproval,
    pub profile: TransitApproval,
    pub matrix: TransitApproval,
    pub binding: TransitApproval,
    pub agent_signers: Vec<SignerRecord>,
}

/// Hardware facts and binding records are explicit operator input. Evidence retains its Agent signature.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorityDraft {
    pub agent_id: Identifier,
    pub scope: AuthorityScope,
    pub revision_id: Identifier,
    pub revision_number: PositiveInteger,
    pub effective_at: AuthorityTime,
    pub expires_at: AuthorityTime,
    pub profiles: Vec<DriverProfile>,
    pub certification_rows: Vec<HardwareCertificationMatrixRow>,
    pub bindings: Vec<BindingRevision>,
    pub evidence: Vec<SignedDeviceTestEvidence>,
    pub activations: Vec<BindingActivation>,
}

impl AuthorityApproval {
    fn validate(&self, now: AuthorityTime) -> AppResult<()> {
        self.scope.validate()?;
        if !valid_openbao_name(&self.transit_mount) || self.agent_signers.len() > 125 {
            return Err(AppError::bad_request("The authority approval is invalid."));
        }
        let mut key_names = BTreeSet::new();
        let mut public_keys = BTreeSet::new();
        for (key, purpose) in [
            (&self.root, SignerPurpose::AuthorityRevision),
            (&self.profile, SignerPurpose::DriverProfile),
            (&self.matrix, SignerPurpose::CertificationMatrix),
            (&self.binding, SignerPurpose::BindingRevision),
        ] {
            key.signer.validate()?;
            if !key_names.insert(&key.key_name)
                || !public_keys.insert(*key.signer.public_key.as_bytes())
            {
                return Err(AppError::bad_request(
                    "Each Controller signing purpose requires a distinct Transit key and public key.",
                ));
            }
            if key.signer.purpose != purpose
                || !valid_openbao_name(&key.key_name)
                || key.key_version.get() > i32::MAX as u32
                || key.signer.state != inari_gateway::device_authority::SignerState::Active
                || now < key.signer.not_before
                || key
                    .signer
                    .not_after
                    .is_some_and(|end| now >= end)
            {
                return Err(AppError::bad_request(
                    "A Controller signing approval is not current for its purpose.",
                ));
            }
        }
        for signer in &self.agent_signers {
            signer.validate()?;
            if !public_keys.insert(*signer.public_key.as_bytes()) {
                return Err(AppError::bad_request(
                    "An Agent signing key cannot share another signing purpose's key material.",
                ));
            }
            if !matches!(
                signer.purpose,
                SignerPurpose::DeviceObservation | SignerPurpose::DeviceTestEvidence
            ) {
                return Err(AppError::bad_request("An Agent signer has a Controller purpose."));
            }
        }
        Ok(())
    }

    async fn key(
        &self,
        approved: &TransitApproval,
        client: Arc<OpenBaoClient>,
    ) -> AppResult<AuthoritySigningKey> {
        AuthoritySigningKey::load(
            client,
            self.transit_mount.clone(),
            approved.key_name.clone(),
            approved.key_version,
            approved.signer.clone(),
        )
        .await
    }
}

fn prepare(
    draft: AuthorityDraft,
    approval: &AuthorityApproval,
    now: AuthorityTime,
) -> AppResult<AuthorityBundle> {
    approval.validate(now)?;
    if draft.agent_id != approval.agent_id || draft.scope != approval.scope {
        return Err(AppError::forbidden(
            "The draft differs from the approved Agent or Binding Scope.",
        ));
    }
    if draft.effective_at > now || draft.expires_at <= now || draft.expires_at <= draft.effective_at
    {
        return Err(AppError::bad_request(
            "The authority revision needs a current, bounded lifetime.",
        ));
    }
    let mut signers = vec![
        approval.profile.signer.clone(),
        approval.matrix.signer.clone(),
        approval.binding.signer.clone(),
    ];
    signers.extend(approval.agent_signers.iter().cloned());
    if signers
        .iter()
        .any(|signer| signer.key_id == approval.root.signer.key_id)
    {
        return Err(AppError::bad_request("The manifest cannot contain the authority root key."));
    }
    let manifest = AuthorityManifest {
        contract: AuthorityContract::V1,
        agent_id: draft.agent_id,
        scope: draft.scope,
        signers,
        profiles: draft
            .profiles
            .into_iter()
            .map(|profile| {
                Ok(SignedDriverProfile::from_signature(
                    CanonicalRecord::new(profile)?,
                    approval.profile.signer.key_id.clone(),
                    HexBytes::new([0; 64]),
                ))
            })
            .collect::<AppResult<_>>()?,
        certification_rows: draft
            .certification_rows
            .into_iter()
            .map(|row| {
                Ok(SignedHardwareCertificationMatrixRow::from_signature(
                    CanonicalRecord::new(row)?,
                    approval.matrix.signer.key_id.clone(),
                    HexBytes::new([0; 64]),
                ))
            })
            .collect::<AppResult<_>>()?,
        bindings: draft
            .bindings
            .into_iter()
            .map(|binding| {
                Ok(SignedBindingRevision::from_signature(
                    CanonicalRecord::new(binding)?,
                    approval.binding.signer.key_id.clone(),
                    HexBytes::new([0; 64]),
                ))
            })
            .collect::<AppResult<_>>()?,
        evidence: draft.evidence,
        activations: draft.activations,
    };
    manifest.validate()?;
    for evidence in &manifest.evidence {
        let signer = approval
            .agent_signers
            .iter()
            .find(|signer| signer.key_id == evidence.signer_key_id)
            .ok_or_else(|| {
                AppError::forbidden("The Device Test evidence signer is not approved.")
            })?;
        evidence.verify(signer, now)?;
    }
    let revision = CanonicalRecord::new(AuthorityRevision {
        revision_id: draft.revision_id,
        revision_number: draft.revision_number,
        manifest_digest: manifest.digest()?,
        effective_at: draft.effective_at,
        expires_at: Some(draft.expires_at),
    })?;
    Ok(AuthorityBundle {
        revision: SignedAuthorityRevision::from_signature(
            revision,
            approval.root.signer.key_id.clone(),
            HexBytes::new([0; 64]),
        ),
        manifest,
    })
}

/// Verify all signed records and the activation graph before returning a publishable bundle.
pub async fn sign_bundle(
    draft: AuthorityDraft,
    approval: &AuthorityApproval,
    client: Arc<OpenBaoClient>,
    now: AuthorityTime,
) -> AppResult<AuthorityBundle> {
    let mut bundle = prepare(draft, approval, now)?;
    let root = approval
        .key(&approval.root, client.clone())
        .await?;
    let profile = approval
        .key(&approval.profile, client.clone())
        .await?;
    let matrix = approval
        .key(&approval.matrix, client.clone())
        .await?;
    let binding = approval
        .key(&approval.binding, client)
        .await?;
    for item in &mut bundle.manifest.profiles {
        let record = CanonicalRecord::new(item.profile.clone())?;
        item.signature = profile.sign(&record, now).await?;
    }
    for item in &mut bundle.manifest.certification_rows {
        let record = CanonicalRecord::new(item.row.clone())?;
        item.signature = matrix.sign(&record, now).await?;
    }
    for item in &mut bundle.manifest.bindings {
        let record = CanonicalRecord::new(item.revision.clone())?;
        item.signature = binding.sign(&record, now).await?;
    }
    bundle.revision.revision.manifest_digest = bundle.manifest.digest()?;
    let record = CanonicalRecord::new(bundle.revision.revision)?;
    let signature = root.sign(&record, now).await?;
    bundle.revision = SignedAuthorityRevision::from_signature(
        record,
        approval.root.signer.key_id.clone(),
        signature,
    );
    bundle.verify(&approval.root.signer, approval.agent_id.as_str(), &approval.scope, now)?;
    Ok(bundle)
}

fn read_document<T: DeserializeOwned>(path: &Path) -> AppResult<T> {
    let file = std::fs::File::open(path)
        .map_err(|error| io_error("The authority input could not be opened.", error))?;
    let mut bytes = Vec::new();
    file.take((MAX_DOCUMENT_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| io_error("The authority input could not be read.", error))?;
    if bytes.len() > MAX_DOCUMENT_BYTES {
        return Err(AppError::bad_request("The authority input exceeds 4 MiB."));
    }
    serde_json::from_slice(&bytes).map_err(|source| {
        AppError::bad_request("The authority input is invalid.").with_source(source)
    })
}

fn write_bundle(path: &Path, bundle: &AuthorityBundle) -> AppResult<()> {
    let bytes = serde_json::to_vec_pretty(bundle).map_err(|source| {
        AppError::internal(
            "authority_serialization",
            "The signed authority bundle could not be encoded.",
        )
        .with_source(source)
    })?;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| io_error("The authority output must be a new file.", error))?;
    if let Err(error) = file
        .write_all(&bytes)
        .and_then(|()| file.sync_all())
    {
        drop(file);
        let _ = std::fs::remove_file(path);
        return Err(io_error("The signed authority output could not be stored.", error));
    }
    Ok(())
}

fn io_error(message: &'static str, source: std::io::Error) -> AppError {
    AppError::internal("authority_document_io", message).with_source(source)
}

pub async fn sign_bundle_files(
    config: OpenBaoConfig,
    draft: &Path,
    approval: &Path,
    output: &Path,
) -> AppResult<AuthorityDigest> {
    let draft = read_document(draft)?;
    let approval = read_document(approval)?;
    let now = AuthorityTime::new(Utc::now().trunc_subsecs(6))?;
    let client = Arc::new(OpenBaoClient::load(config).await?);
    let bundle = sign_bundle(draft, &approval, client, now).await?;
    bundle.verify(
        &approval.root.signer,
        approval.agent_id.as_str(),
        &approval.scope,
        AuthorityTime::new(Utc::now().trunc_subsecs(6))?,
    )?;
    write_bundle(output, &bundle)?;
    Ok(bundle.revision.digest)
}

#[cfg(test)]
mod tests;
