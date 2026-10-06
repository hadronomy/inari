use std::collections::BTreeMap;
use std::num::NonZeroU32;
use std::sync::Arc;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::{Signature, VerifyingKey};
use inari_gateway::device_authority::{
    AuthorityTime, CanonicalRecord, ControllerRecordPayload, HexBytes, SignerPurpose, SignerRecord,
    SignerState,
};
use serde::{Deserialize, Serialize};

use super::OpenBaoClient;
use crate::config::valid_openbao_name;
use crate::error::{AppError, AppResult};

/// One approved Controller key version. Private key material stays in OpenBao.
pub struct AuthoritySigningKey {
    client: Arc<OpenBaoClient>,
    transit_mount: String,
    key_name: String,
    key_version: NonZeroU32,
    signer: SignerRecord,
}

impl std::fmt::Debug for AuthoritySigningKey {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AuthoritySigningKey")
            .field("key_name", &self.key_name)
            .field("key_version", &self.key_version)
            .field("signer_key_id", &self.signer.key_id)
            .finish_non_exhaustive()
    }
}

impl AuthoritySigningKey {
    /// Require an approved public key and an exact, nonexportable Ed25519 Transit version.
    pub async fn load(
        client: Arc<OpenBaoClient>,
        transit_mount: String,
        key_name: String,
        key_version: NonZeroU32,
        signer: SignerRecord,
    ) -> AppResult<Self> {
        signer.validate()?;
        if !valid_openbao_name(&transit_mount)
            || !valid_openbao_name(&key_name)
            || key_version.get() > i32::MAX as u32
            || !matches!(
                signer.purpose,
                SignerPurpose::AuthorityRevision
                    | SignerPurpose::DriverProfile
                    | SignerPurpose::CertificationMatrix
                    | SignerPurpose::BindingRevision
            )
        {
            return Err(AppError::bad_request("The approved Controller Transit key is invalid."));
        }
        let key = Self { client, transit_mount, key_name, key_version, signer };
        key.verify_metadata().await?;
        Ok(key)
    }

    #[must_use]
    pub fn signer(&self) -> &SignerRecord {
        &self.signer
    }

    /// Sign validated Controller bytes with the approved version and verify the returned signature.
    pub async fn sign<T: ControllerRecordPayload>(
        &self,
        record: &CanonicalRecord<T>,
        now: AuthorityTime,
    ) -> AppResult<HexBytes<64>> {
        if T::PURPOSE != self.signer.purpose
            || self.signer.state != SignerState::Active
            || now < self.signer.not_before
            || self
                .signer
                .not_after
                .is_some_and(|end| now >= end)
        {
            return Err(AppError::forbidden(
                "The Controller authority signing key is not current for this purpose.",
            ));
        }
        self.verify_metadata().await?;
        let response: SignResponse = self
            .client
            .post(
                &format!("v1/{}/sign/{}", self.transit_mount, self.key_name),
                &SignRequest {
                    input: STANDARD.encode(record.as_bytes()),
                    key_version: self.key_version.get(),
                    prehashed: false,
                },
            )
            .await?;
        let encoded = response
            .data
            .signature
            .strip_prefix(&format!("vault:v{}:", self.key_version))
            .ok_or_else(|| invalid_response("OpenBao returned another authority key version."))?;
        let bytes: [u8; 64] = STANDARD
            .decode(encoded)
            .ok()
            .and_then(|bytes| bytes.try_into().ok())
            .ok_or_else(|| invalid_response("OpenBao returned an invalid Ed25519 signature."))?;
        let key = VerifyingKey::from_bytes(self.signer.public_key.as_bytes())
            .map_err(|_| invalid_response("The approved authority public key is invalid."))?;
        key.verify_strict(record.as_bytes(), &Signature::from_bytes(&bytes))
            .map_err(|_| invalid_response("OpenBao signed with an unapproved authority key."))?;
        Ok(HexBytes::new(bytes))
    }

    async fn verify_metadata(&self) -> AppResult<()> {
        let response: KeyResponse = self
            .client
            .get(&format!("v1/{}/keys/{}", self.transit_mount, self.key_name))
            .await?;
        let metadata = response.data;
        if metadata.name != self.key_name
            || metadata.key_type != "ed25519"
            || metadata.derived
            || metadata.exportable
            || metadata.allow_plaintext_backup
            || !metadata.supports_signing
            || metadata.min_encryption_version > self.key_version.get()
        {
            return Err(invalid_response(
                "The authority Transit key no longer meets its approval.",
            ));
        }
        let public = metadata
            .keys
            .get(&self.key_version.to_string())
            .and_then(|version| {
                STANDARD
                    .decode(&version.public_key)
                    .ok()
            })
            .ok_or_else(|| {
                invalid_response("The approved authority key version is absent or invalid.")
            })?;
        if public.as_slice() != self.signer.public_key.as_bytes() {
            return Err(invalid_response(
                "The authority Transit public key differs from its approval.",
            ));
        }
        Ok(())
    }
}

fn invalid_response(message: &'static str) -> AppError {
    AppError::service_unavailable(message)
}

#[derive(Deserialize)]
struct KeyResponse {
    data: KeyMetadata,
}

#[derive(Deserialize)]
struct KeyMetadata {
    name: String,
    #[serde(rename = "type")]
    key_type: String,
    derived: bool,
    exportable: bool,
    allow_plaintext_backup: bool,
    supports_signing: bool,
    min_encryption_version: u32,
    keys: BTreeMap<String, KeyVersion>,
}

#[derive(Deserialize)]
struct KeyVersion {
    public_key: String,
}

#[derive(Serialize)]
struct SignRequest {
    input: String,
    key_version: u32,
    prehashed: bool,
}

#[derive(Deserialize)]
struct SignResponse {
    data: SignData,
}

#[derive(Deserialize)]
struct SignData {
    signature: String,
}

#[cfg(test)]
mod tests;
