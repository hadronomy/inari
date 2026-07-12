use chrono::{Duration, Utc};
use inari_gateway::certificate::{CertificateIssuer, CertificateRequest};
use inari_gateway::protocol::{CertificateBootstrapAuth, CertificateBootstrapAuthKind};
use inari_gateway::{GatewayError, GatewayResult};
use jsonwebtoken::{Algorithm, EncodingKey, Header};
use serde::Serialize;
use url::Url;

use crate::config::{ManagedGatewayCertificateConfig, StepCaSigningAlgorithm};
use crate::{AppError, AppResult};

pub struct StepCaIssuer {
    audience: String,
    provisioner: String,
    key_id: String,
    algorithm: Algorithm,
    signing_key: EncodingKey,
    token_ttl: std::time::Duration,
}

impl std::fmt::Debug for StepCaIssuer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("StepCaIssuer")
            .field("audience", &self.audience)
            .field("provisioner", &self.provisioner)
            .field("key_id", &self.key_id)
            .field("algorithm", &self.algorithm)
            .field("signing_key", &"<redacted>")
            .field("token_ttl", &self.token_ttl)
            .finish()
    }
}

impl StepCaIssuer {
    pub async fn load(config: &ManagedGatewayCertificateConfig) -> AppResult<Self> {
        let base_url = config
            .step_ca_base_url
            .clone()
            .ok_or_else(|| {
                AppError::internal("step_ca_configuration", "step-ca base URL is not configured.")
            })?;
        let audience = signing_audience(base_url)?;
        let provisioner = required(&config.step_ca_provisioner, "step-ca provisioner")?;
        let key_id = required(&config.step_ca_key_id, "step-ca key ID")?;
        let key_path = config
            .step_ca_signing_key_file
            .as_ref()
            .ok_or_else(|| {
                AppError::internal(
                    "step_ca_configuration",
                    "step-ca provisioner signing key is not configured.",
                )
            })?;
        let key = tokio::fs::read(key_path)
            .await
            .map_err(|source| {
                AppError::internal(
                    "step_ca_signing_key",
                    "The step-ca provisioner signing key could not be read.",
                )
                .with_source(source)
            })?;
        let (algorithm, signing_key) = match config.step_ca_signing_algorithm {
            StepCaSigningAlgorithm::EdDsa => (
                Algorithm::EdDSA,
                EncodingKey::from_ed_pem(&key).map_err(|source| {
                    AppError::internal(
                        "step_ca_signing_key",
                        "The step-ca Ed25519 signing key is invalid.",
                    )
                    .with_source(source)
                })?,
            ),
            StepCaSigningAlgorithm::Es256 => (
                Algorithm::ES256,
                EncodingKey::from_ec_pem(&key).map_err(|source| {
                    AppError::internal(
                        "step_ca_signing_key",
                        "The step-ca P-256 signing key is invalid.",
                    )
                    .with_source(source)
                })?,
            ),
        };
        Ok(Self {
            audience,
            provisioner,
            key_id,
            algorithm,
            signing_key,
            token_ttl: config.step_ca_token_ttl,
        })
    }
}

impl CertificateIssuer for StepCaIssuer {
    fn issue(&self, request: &CertificateRequest) -> GatewayResult<CertificateBootstrapAuth> {
        let issued_at = Utc::now();
        let expires_at = issued_at
            + Duration::from_std(self.token_ttl)
                .map_err(|_| GatewayError::InvalidInput("step-ca token TTL is invalid".into()))?;
        let mut entropy = [0_u8; 32];
        getrandom::fill(&mut entropy)
            .map_err(|error| GatewayError::Unavailable(format!("token entropy failed: {error}")))?;
        let claims = StepCaClaims {
            issuer: &self.provisioner,
            subject: request.agent_id.as_str(),
            audience: &self.audience,
            expires_at: expires_at.timestamp(),
            not_before: issued_at.timestamp(),
            issued_at: issued_at.timestamp(),
            jwt_id: hex::encode(entropy),
            sans: &request.authorized_sans,
            confirmation: Confirmation { csr_fingerprint: &request.csr_fingerprint },
        };
        let mut header = Header::new(self.algorithm);
        header.kid = Some(self.key_id.clone());
        header.typ = Some("JWT".into());
        let token = jsonwebtoken::encode(&header, &claims, &self.signing_key).map_err(|error| {
            GatewayError::Unavailable(format!("step-ca token signing failed: {error}"))
        })?;
        Ok(CertificateBootstrapAuth {
            kind: CertificateBootstrapAuthKind::Ott,
            token: Some(token),
            expires_at: Some(expires_at),
        })
    }
}

#[derive(Serialize)]
struct StepCaClaims<'a> {
    #[serde(rename = "iss")]
    issuer: &'a str,
    #[serde(rename = "sub")]
    subject: &'a str,
    #[serde(rename = "aud")]
    audience: &'a str,
    #[serde(rename = "exp")]
    expires_at: i64,
    #[serde(rename = "nbf")]
    not_before: i64,
    #[serde(rename = "iat")]
    issued_at: i64,
    #[serde(rename = "jti")]
    jwt_id: String,
    sans: &'a [String],
    #[serde(rename = "cnf")]
    confirmation: Confirmation<'a>,
}

#[derive(Serialize)]
struct Confirmation<'a> {
    #[serde(rename = "x5rt#S256")]
    csr_fingerprint: &'a str,
}

fn signing_audience(mut base_url: Url) -> AppResult<String> {
    if !base_url.path().ends_with('/') {
        base_url.set_path(&format!("{}/", base_url.path()));
    }
    base_url
        .join("1.0/sign")
        .map(|url| url.to_string())
        .map_err(|source| {
            AppError::internal("step_ca_configuration", "step-ca signing URL is invalid.")
                .with_source(source)
        })
}

fn required(value: &Option<String>, label: &str) -> AppResult<String> {
    value
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| {
            AppError::internal("step_ca_configuration", format!("{label} is not configured."))
        })
}

#[cfg(test)]
mod tests {
    use base64::Engine;
    use ed25519_dalek::SigningKey;
    use ed25519_dalek::pkcs8::EncodePrivateKey;
    use inari_gateway::certificate::{CertificateIssuer, CertificateRequest};
    use jsonwebtoken::{Algorithm, EncodingKey};
    use rand_core::OsRng;
    use serde_json::Value;

    use super::StepCaIssuer;

    #[test]
    fn tokens_are_short_lived_unique_and_bound_to_the_csr() {
        let key = SigningKey::generate(&mut OsRng)
            .to_pkcs8_der()
            .expect("test key should encode");
        let issuer = StepCaIssuer {
            audience: "https://ca.example.com/1.0/sign".into(),
            provisioner: "inari-agents".into(),
            key_id: "provisioner-kid".into(),
            algorithm: Algorithm::EdDSA,
            signing_key: EncodingKey::from_ed_der(key.as_bytes()),
            token_ttl: std::time::Duration::from_secs(300),
        };
        let request = CertificateRequest {
            agent_id: "agt_test"
                .parse()
                .expect("agent ID should parse"),
            authorized_sans: vec!["urn:inari:agt_test".into()],
            csr_fingerprint: "csr-fingerprint".into(),
        };
        let first = issuer
            .issue(&request)
            .expect("token should issue");
        let second = issuer
            .issue(&request)
            .expect("token should issue");
        assert_ne!(first.token, second.token);
        let claims = claims(
            first
                .token
                .as_deref()
                .expect("token should be present"),
        );
        assert_eq!(claims["iss"], "inari-agents");
        assert_eq!(claims["sub"], "agt_test");
        assert_eq!(claims["aud"], "https://ca.example.com/1.0/sign");
        assert_eq!(claims["sans"][0], "urn:inari:agt_test");
        assert_eq!(claims["cnf"]["x5rt#S256"], "csr-fingerprint");
        assert!(claims["exp"].as_i64().unwrap() - claims["iat"].as_i64().unwrap() <= 300);
    }

    fn claims(token: &str) -> Value {
        let payload = token
            .split('.')
            .nth(1)
            .expect("JWT should contain claims");
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(payload)
            .expect("claims should decode");
        serde_json::from_slice(&payload).expect("claims should be JSON")
    }
}
