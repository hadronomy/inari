use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{DateTime, Utc};
use ed25519_dalek::pkcs8::DecodePrivateKey;
use ed25519_dalek::{Signer, SigningKey};
use hpke::aead::AesGcm256;
use hpke::kdf::HkdfSha256;
use hpke::kem::X25519HkdfSha256;
use hpke::{Deserializable, OpModeS, Serializable};
use inari_gateway::protocol::{
    AgentManagedScope, ControllerCommand, DispatchDeviceWork, DispatchEncryptionKey,
    DispatchHpkeSuite, MANAGED_DISPATCH_VERSION, ManagedDispatchAuthenticatedData,
    ManagedDispatchClaims, ManagedDispatchEnrollment, ManagedWorkId, ManagedWorkSubmission,
    SealedManagedDispatch,
};
use jsonwebtoken::jwk::Jwk;
use serde::Serialize;

use crate::config::ManagedGatewayDispatchConfig;
use crate::error::{AppError, AppResult};

const DISPATCH_JWS_TYPE: &str = "application/inari-dispatch+jws";

pub struct ManagedDispatchSigner {
    issuer: String,
    key_id: String,
    epoch: u64,
    envelope_ttl: Duration,
    signing_key: SigningKey,
    verification_jwk: Jwk,
}

impl std::fmt::Debug for ManagedDispatchSigner {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ManagedDispatchSigner")
            .field("issuer", &self.issuer)
            .field("key_id", &self.key_id)
            .field("epoch", &self.epoch)
            .field("envelope_ttl", &self.envelope_ttl)
            .field("signing_key", &"<redacted>")
            .finish()
    }
}

impl ManagedDispatchSigner {
    pub async fn load(
        config: &ManagedGatewayDispatchConfig,
        controller_instance_id: &str,
    ) -> AppResult<Self> {
        let key_id = config
            .signing_key_id
            .as_deref()
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                AppError::internal(
                    "managed_dispatch_configuration",
                    "The managed dispatch signing key ID is not configured.",
                )
            })?
            .to_owned();
        let key_path = config
            .signing_key_file
            .as_ref()
            .ok_or_else(|| {
                AppError::internal(
                    "managed_dispatch_configuration",
                    "The managed dispatch signing key file is not configured.",
                )
            })?;
        let key_bytes = tokio::fs::read(key_path)
            .await
            .map_err(|source| {
                AppError::internal(
                    "managed_dispatch_signing_key",
                    "The managed dispatch signing key could not be read.",
                )
                .with_source(source)
            })?;
        let signing_key = std::str::from_utf8(&key_bytes)
            .ok()
            .and_then(|pem| SigningKey::from_pkcs8_pem(pem).ok())
            .or_else(|| SigningKey::from_pkcs8_der(&key_bytes).ok())
            .ok_or_else(|| {
                AppError::internal(
                    "managed_dispatch_signing_key",
                    "The managed dispatch Ed25519 signing key is invalid.",
                )
            })?;
        let public_key = URL_SAFE_NO_PAD.encode(signing_key.verifying_key().as_bytes());
        let verification_jwk = serde_json::from_value(serde_json::json!({
            "kty": "OKP",
            "crv": "Ed25519",
            "x": public_key,
            "kid": key_id,
            "alg": "EdDSA",
            "use": "sig"
        }))?;
        Ok(Self {
            issuer: controller_instance_id.to_owned(),
            key_id,
            epoch: config.epoch,
            envelope_ttl: config.envelope_ttl,
            signing_key,
            verification_jwk,
        })
    }

    #[must_use]
    pub fn enrollment(&self, scope: AgentManagedScope) -> ManagedDispatchEnrollment {
        ManagedDispatchEnrollment {
            scope,
            issuer: self.issuer.clone(),
            epoch: self.epoch,
            verification_jwk: self.verification_jwk.clone(),
        }
    }

    pub fn command(
        &self,
        managed_work_id: ManagedWorkId,
        idempotency_key: String,
        submission: ManagedWorkSubmission,
        recipient_key: &DispatchEncryptionKey,
        work_expires_at: DateTime<Utc>,
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
    ) -> AppResult<ControllerCommand> {
        let dispatch_expires_at = issued_at
            + chrono::TimeDelta::from_std(self.envelope_ttl).map_err(|source| {
                AppError::internal(
                    "managed_dispatch_deadline",
                    "The managed dispatch deadline is invalid.",
                )
                .with_source(source)
            })?;
        let expires_at = std::cmp::min(dispatch_expires_at, work_expires_at);
        let authenticated_data = ManagedDispatchAuthenticatedData {
            organization_id: submission
                .work
                .scope
                .organization_id
                .clone(),
            site_id: submission.work.scope.site_id.clone(),
            agent_id: submission.work.scope.agent_id.clone(),
            managed_work_id: managed_work_id.clone(),
            idempotency_key,
            payload_fingerprint: submission.payload_fingerprint.clone(),
            dispatch_epoch: self.epoch,
            sequence,
            issued_at: issued_at.timestamp(),
            expires_at: expires_at.timestamp(),
        };
        let claims = ManagedDispatchClaims {
            issuer: self.issuer.clone(),
            audience: submission.work.scope.agent_id.clone(),
            authenticated_data: authenticated_data.clone(),
            work: submission.work,
        };
        let signed_envelope = self.sign(&claims)?;
        let sealed_envelope =
            seal_dispatch(signed_envelope.as_bytes(), &authenticated_data, recipient_key)?;
        Ok(ControllerCommand::DispatchDeviceWork {
            message_id,
            command_id,
            sequence,
            issued_at,
            payload: DispatchDeviceWork { managed_work_id, authenticated_data, sealed_envelope },
        })
    }

    fn sign(&self, claims: &ManagedDispatchClaims) -> AppResult<String> {
        let protected = DispatchHeader {
            algorithm: "EdDSA",
            key_id: &self.key_id,
            content_type: DISPATCH_JWS_TYPE,
        };
        let protected = serde_json_canonicalizer::to_vec(&protected).map_err(|source| {
            AppError::internal(
                "managed_dispatch_serialization",
                "The managed dispatch header could not be canonicalized.",
            )
            .with_source(source)
        })?;
        let payload = serde_json_canonicalizer::to_vec(claims).map_err(|source| {
            AppError::internal(
                "managed_dispatch_serialization",
                "The managed dispatch claims could not be canonicalized.",
            )
            .with_source(source)
        })?;
        let protected = URL_SAFE_NO_PAD.encode(protected);
        let payload = URL_SAFE_NO_PAD.encode(payload);
        let signing_input = format!("{protected}.{payload}");
        let signature = self
            .signing_key
            .sign(signing_input.as_bytes());
        Ok(format!("{signing_input}.{}", URL_SAFE_NO_PAD.encode(signature.to_bytes())))
    }
}

fn seal_dispatch(
    signed_envelope: &[u8],
    authenticated_data: &ManagedDispatchAuthenticatedData,
    recipient_key: &DispatchEncryptionKey,
) -> AppResult<SealedManagedDispatch> {
    let public_key = URL_SAFE_NO_PAD
        .decode(&recipient_key.public_key_base64url)
        .map_err(|_| {
            AppError::bad_request("The Agent dispatch public key is not valid base64url.")
        })?;
    let public_key =
        <X25519HkdfSha256 as hpke::Kem>::PublicKey::from_bytes(&public_key).map_err(|source| {
            AppError::bad_request("The Agent dispatch public key is invalid.").with_source(source)
        })?;
    let info = dispatch_info(authenticated_data.agent_id.as_str(), &recipient_key.key_id);
    let (encapsulated_key, mut context) = hpke::setup_sender::<
        AesGcm256,
        HkdfSha256,
        X25519HkdfSha256,
    >(&OpModeS::Base, &public_key, info.as_bytes())
    .map_err(|source| {
        AppError::internal(
            "managed_dispatch_encryption",
            "The managed dispatch HPKE context could not be created.",
        )
        .with_source(source)
    })?;
    let aad = serde_json_canonicalizer::to_vec(authenticated_data).map_err(|source| {
        AppError::internal(
            "managed_dispatch_serialization",
            "The managed dispatch authenticated data could not be canonicalized.",
        )
        .with_source(source)
    })?;
    let ciphertext = context
        .seal(signed_envelope, &aad)
        .map_err(|source| {
            AppError::internal(
                "managed_dispatch_encryption",
                "The managed dispatch could not be encrypted.",
            )
            .with_source(source)
        })?;
    Ok(SealedManagedDispatch {
        protocol_version: MANAGED_DISPATCH_VERSION,
        key_id: recipient_key.key_id.clone(),
        suite: DispatchHpkeSuite::DhkemX25519HkdfSha256HkdfSha256Aes256Gcm,
        encapsulated_key_base64url: URL_SAFE_NO_PAD.encode(encapsulated_key.to_bytes()),
        ciphertext_base64url: URL_SAFE_NO_PAD.encode(ciphertext),
    })
}

pub(crate) fn dispatch_info(agent_id: &str, recipient_key_id: &str) -> String {
    format!("inari-managed-dispatch\0v{MANAGED_DISPATCH_VERSION}\0{agent_id}\0{recipient_key_id}")
}

#[derive(Serialize)]
struct DispatchHeader<'a> {
    #[serde(rename = "alg")]
    algorithm: &'a str,
    #[serde(rename = "kid")]
    key_id: &'a str,
    #[serde(rename = "typ")]
    content_type: &'a str,
}

#[cfg(test)]
mod tests {
    use base64::Engine;
    use chrono::{TimeDelta, Utc};
    use ed25519_dalek::{Signature, Verifier};
    use hpke::aead::AesGcm256;
    use hpke::kdf::HkdfSha256;
    use hpke::kem::X25519HkdfSha256;
    use hpke::{Deserializable, Kem as _, OpModeR, Serializable};
    use inari_gateway::protocol::{
        ControllerCommand, DispatchEncryptionKey, DispatchKem, ManagedDeviceWork,
        ManagedDispatchClaims, ManagedDocument, ManagedWorkScope, ManagedWorkSubmission,
        ReportBindingClaim, ReportPrintOrigin, ReportRoute, ReportSource,
    };
    use rand_core::OsRng;

    use super::{ManagedDispatchSigner, dispatch_info};

    #[test]
    fn dispatch_envelope_is_canonical_and_bound_to_the_command_sequence() {
        let signing_key = ed25519_dalek::SigningKey::generate(&mut OsRng);
        let verifying_key = signing_key.verifying_key();
        let signer = ManagedDispatchSigner {
            issuer: "controller-primary".into(),
            key_id: "dispatch-key-1".into(),
            epoch: 7,
            envelope_ttl: std::time::Duration::from_secs(120),
            verification_jwk: serde_json::from_value(serde_json::json!({
                "kty": "OKP",
                "crv": "Ed25519",
                "x": base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(verifying_key.as_bytes()),
                "kid": "dispatch-key-1",
                "alg": "EdDSA",
                "use": "sig"
            }))
            .expect("verification JWK should parse"),
            signing_key,
        };
        let issued_at = Utc::now();
        let (recipient_private_key, recipient_public_key) = X25519HkdfSha256::gen_keypair();
        let recipient_key = DispatchEncryptionKey {
            key_id: "agent-dispatch-key-1".into(),
            kem: DispatchKem::DhkemX25519HkdfSha256,
            public_key_base64url: base64::engine::general_purpose::URL_SAFE_NO_PAD
                .encode(recipient_public_key.to_bytes()),
        };
        let work_id: inari_gateway::protocol::ManagedWorkId = "mw_dispatch_test"
            .parse()
            .expect("Managed Work ID should parse");
        let command = signer
            .command(
                work_id.clone(),
                "report:manual:42:1".into(),
                submission(),
                &recipient_key,
                issued_at + TimeDelta::minutes(5),
                "msg_dispatch_test".into(),
                "job_dispatch_test".into(),
                42,
                issued_at,
            )
            .expect("dispatch command should sign");
        let ControllerCommand::DispatchDeviceWork { payload, .. } = command else {
            panic!("expected a Managed Work dispatch command");
        };
        let encapsulated_key = <X25519HkdfSha256 as hpke::Kem>::EncappedKey::from_bytes(&decode(
            &payload
                .sealed_envelope
                .encapsulated_key_base64url,
        ))
        .expect("encapsulated key should parse");
        let info = dispatch_info("agt_example", "agent-dispatch-key-1");
        let mut receiver = hpke::setup_receiver::<AesGcm256, HkdfSha256, X25519HkdfSha256>(
            &OpModeR::Base,
            &recipient_private_key,
            &encapsulated_key,
            info.as_bytes(),
        )
        .expect("receiver context should open");
        let aad = serde_json_canonicalizer::to_vec(&payload.authenticated_data)
            .expect("authenticated data should canonicalize");
        let signed_envelope = receiver
            .open(
                &decode(
                    &payload
                        .sealed_envelope
                        .ciphertext_base64url,
                ),
                &aad,
            )
            .expect("dispatch should decrypt");
        let signed_envelope =
            std::str::from_utf8(&signed_envelope).expect("dispatch JWS should be UTF-8");
        let segments = signed_envelope
            .split('.')
            .collect::<Vec<_>>();
        assert_eq!(segments.len(), 3);
        let signing_input = format!("{}.{}", segments[0], segments[1]);
        let signature = decode(segments[2]);
        verifying_key
            .verify(
                signing_input.as_bytes(),
                &Signature::from_slice(&signature).expect("signature should have a fixed size"),
            )
            .expect("dispatch signature should verify");

        let protected: serde_json::Value =
            serde_json::from_slice(&decode(segments[0])).expect("header should parse");
        assert_eq!(protected["alg"], "EdDSA");
        assert_eq!(protected["kid"], "dispatch-key-1");
        assert_eq!(protected["typ"], "application/inari-dispatch+jws");
        let payload_bytes = decode(segments[1]);
        let claims: ManagedDispatchClaims =
            serde_json::from_slice(&payload_bytes).expect("claims should parse");
        assert_eq!(
            claims
                .authenticated_data
                .managed_work_id,
            work_id
        );
        assert_eq!(claims.authenticated_data.dispatch_epoch, 7);
        assert_eq!(claims.authenticated_data.sequence, 42);
        assert_eq!(
            claims
                .authenticated_data
                .idempotency_key,
            "report:manual:42:1"
        );
        assert_eq!(claims.authenticated_data, payload.authenticated_data);
        assert_eq!(claims.audience.as_str(), "agt_example");
        assert_eq!(
            payload_bytes,
            serde_json_canonicalizer::to_vec(&claims).expect("claims should canonicalize")
        );
    }

    fn submission() -> ManagedWorkSubmission {
        ManagedWorkSubmission {
            contract_major: 1,
            preflight_id: "mpf_dispatch_test"
                .parse()
                .expect("preflight ID should parse"),
            payload_fingerprint: "c".repeat(64),
            work: ManagedDeviceWork {
                contract_major: 1,
                scope: ManagedWorkScope {
                    database: "production".into(),
                    company_id: "7".into(),
                    organization_id: "org_example"
                        .parse()
                        .expect("Organization ID should parse"),
                    site_id: "site_example"
                        .parse()
                        .expect("Site ID should parse"),
                    agent_id: "agt_example"
                        .parse()
                        .expect("Agent ID should parse"),
                },
                print_intent_id: "pi_v1_dispatch_test"
                    .parse()
                    .expect("Print Intent ID should parse"),
                device_id: "dev_report_printer"
                    .parse()
                    .expect("Device ID should parse"),
                origin: ReportPrintOrigin {
                    binding: ReportBindingClaim {
                        report_binding_id: "binding-1".into(),
                        binding_revision_id: "revision-1".into(),
                        report_action_id: "sale.action_report_saleorder".into(),
                        report_contract_digest: "a".repeat(64),
                        template_digest: "b".repeat(64),
                        command_profile_id: None,
                        layout_profile_id: None,
                        hardware_matrix_digest: None,
                    },
                    route: ReportRoute::Manual,
                    source: ReportSource::Records {
                        model: "sale.order".into(),
                        ordered_ids: vec![42],
                    },
                    rendered_document_index: 0,
                    copy_ordinal: 1,
                },
                document: ManagedDocument::ReportPdf { content_base64: "JVBERi0xLjQ=".into() },
                normalized_device_options: Default::default(),
            },
        }
    }

    fn decode(value: &str) -> Vec<u8> {
        base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(value)
            .expect("base64url should decode")
    }
}
