use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use jsonwebtoken::jwk::{
    AlgorithmParameters, EllipticCurve, Jwk, KeyAlgorithm, PublicKeyUse, ThumbprintHash,
};
use sha2::{Digest, Sha256};
use x509_parser::certification_request::X509CertificationRequest;
use x509_parser::parse_x509_certificate;
use x509_parser::pem::parse_x509_pem;
use x509_parser::prelude::FromDer;

use crate::protocol::{DispatchEncryptionKey, DispatchKem};
use crate::{GatewayError, GatewayResult};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedIdentity {
    pub key_id: String,
    pub jwk_thumbprint: String,
    pub public_key: [u8; 32],
    pub csr_fingerprint: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedDispatchKey {
    pub key_id: String,
    pub public_key: [u8; 32],
    pub fingerprint: String,
}

pub fn validate_state_signing_key(jwk: &Jwk, identity_key: &[u8; 32]) -> GatewayResult<()> {
    if jwk.common.key_algorithm != Some(KeyAlgorithm::EdDSA)
        || jwk.common.public_key_use != Some(PublicKeyUse::Signature)
        || jwk.common.key_operations.is_some()
    {
        return Err(GatewayError::InvalidInput(
            "Agent State JWK must declare EdDSA and sig".into(),
        ));
    }
    let AlgorithmParameters::OctetKeyPair(parameters) = &jwk.algorithm else {
        return Err(GatewayError::InvalidInput("Agent State JWK must be an OKP key".into()));
    };
    if parameters.curve != EllipticCurve::Ed25519 {
        return Err(GatewayError::InvalidInput("Agent State JWK curve must be Ed25519".into()));
    }
    let public_key = URL_SAFE_NO_PAD
        .decode(&parameters.x)
        .map_err(|_| {
            GatewayError::InvalidInput("Agent State JWK x is not canonical base64url".into())
        })?;
    let public_key: [u8; 32] = public_key.try_into().map_err(|_| {
        GatewayError::InvalidInput("Agent State public keys must be 32 bytes".into())
    })?;
    let verifying_key = ed25519_dalek::VerifyingKey::from_bytes(&public_key)
        .map_err(|_| GatewayError::InvalidInput("Agent State public key is invalid".into()))?;
    if verifying_key.is_weak() || &public_key == identity_key {
        return Err(GatewayError::InvalidInput(
            "Agent State requires a separate, valid signing key".into(),
        ));
    }
    let expected_id = format!("agent_state_{:x}", Sha256::digest(public_key));
    if jwk.common.key_id.as_deref() != Some(expected_id.as_str()) {
        return Err(GatewayError::InvalidInput(
            "Agent State kid must identify its public key".into(),
        ));
    }
    Ok(())
}

pub fn validate_dispatch_key(key: &DispatchEncryptionKey) -> GatewayResult<ValidatedDispatchKey> {
    if key.key_id.is_empty()
        || key.key_id.len() > 256
        || !key
            .key_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(GatewayError::InvalidInput(
            "dispatch key IDs must use 1 to 256 ASCII letters, digits, hyphens, or underscores"
                .into(),
        ));
    }
    if key.kem != DispatchKem::DhkemX25519HkdfSha256 {
        return Err(GatewayError::InvalidInput("unsupported dispatch KEM".into()));
    }
    let public_key = URL_SAFE_NO_PAD
        .decode(key.public_key_base64url.as_bytes())
        .map_err(|_| GatewayError::InvalidInput("dispatch public key is not base64url".into()))?;
    let public_key: [u8; 32] = public_key
        .try_into()
        .map_err(|_| GatewayError::InvalidInput("X25519 public keys must be 32 bytes".into()))?;
    if public_key.iter().all(|byte| *byte == 0) {
        return Err(GatewayError::InvalidInput(
            "X25519 public keys must not be the all-zero value".into(),
        ));
    }
    Ok(ValidatedDispatchKey {
        key_id: key.key_id.clone(),
        public_key,
        fingerprint: URL_SAFE_NO_PAD.encode(Sha256::digest(public_key)),
    })
}

pub fn validate_identity(
    key_id: &str,
    jwk: &Jwk,
    csr_pem: &str,
    certificate_pem: Option<&str>,
) -> GatewayResult<ValidatedIdentity> {
    if jwk.common.key_id.as_deref() != Some(key_id) {
        return Err(GatewayError::InvalidInput("public JWK kid must match key_id".into()));
    }
    if jwk.common.key_algorithm != Some(KeyAlgorithm::EdDSA) {
        return Err(GatewayError::InvalidInput("public JWK alg must be EdDSA".into()));
    }
    if !matches!(jwk.common.public_key_use, None | Some(PublicKeyUse::Signature)) {
        return Err(GatewayError::InvalidInput("public JWK use must be sig".into()));
    }
    let AlgorithmParameters::OctetKeyPair(parameters) = &jwk.algorithm else {
        return Err(GatewayError::InvalidInput("public JWK must be an OKP key".into()));
    };
    if parameters.curve != EllipticCurve::Ed25519 {
        return Err(GatewayError::InvalidInput("public JWK curve must be Ed25519".into()));
    }
    let public_key = URL_SAFE_NO_PAD
        .decode(parameters.x.as_bytes())
        .map_err(|_| GatewayError::InvalidInput("public JWK x is not valid base64url".into()))?;
    let public_key: [u8; 32] = public_key
        .try_into()
        .map_err(|_| GatewayError::InvalidInput("Ed25519 public key must be 32 bytes".into()))?;

    let (_, pem) = parse_x509_pem(csr_pem.as_bytes())
        .map_err(|_| GatewayError::InvalidInput("CSR is not valid PEM".into()))?;
    let (_, csr) = X509CertificationRequest::from_der(&pem.contents)
        .map_err(|_| GatewayError::InvalidInput("CSR is not valid PKCS#10 DER".into()))?;
    csr.verify_signature()
        .map_err(|_| GatewayError::InvalidInput("CSR signature is invalid".into()))?;
    let csr_key = csr
        .certification_request_info
        .subject_pki
        .subject_public_key
        .data
        .as_ref();
    if csr_key != public_key {
        return Err(GatewayError::InvalidInput("CSR public key does not match public JWK".into()));
    }

    if let Some(certificate_pem) = certificate_pem {
        let (_, pem) = parse_x509_pem(certificate_pem.as_bytes())
            .map_err(|_| GatewayError::InvalidInput("certificate is not valid PEM".into()))?;
        let (_, certificate) = parse_x509_certificate(&pem.contents)
            .map_err(|_| GatewayError::InvalidInput("certificate is not valid DER".into()))?;
        if certificate
            .public_key()
            .subject_public_key
            .data
            .as_ref()
            != public_key
        {
            return Err(GatewayError::InvalidInput(
                "certificate public key does not match public JWK".into(),
            ));
        }
    }

    Ok(ValidatedIdentity {
        key_id: key_id.into(),
        jwk_thumbprint: jwk.thumbprint(ThumbprintHash::SHA256),
        public_key,
        csr_fingerprint: URL_SAFE_NO_PAD.encode(Sha256::digest(&pem.contents)),
    })
}

#[cfg(test)]
mod tests {
    use base64::Engine;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use ed25519_dalek::SigningKey;
    use jsonwebtoken::jwk::Jwk;
    use serde_json::json;
    use sha2::{Digest, Sha256};

    use crate::protocol::{DispatchEncryptionKey, DispatchKem};

    use super::{validate_dispatch_key, validate_identity, validate_state_signing_key};

    const CSR: &str = "-----BEGIN CERTIFICATE REQUEST-----\nMIGSMEYCAQAwEzERMA8GA1UEAwwIYWd0X3Rlc3QwKjAFBgMrZXADIQAhvMvqGoKi\nttgqTZhDbzMb8IFPEaHQvEGR9AOkm+qecaAAMAUGAytlcANBAA8BTmcCjYiBRLuZ\nqNcH8/6K/ZYHnbHl7xksiR9pzqqi+jbcKi8gKJ62q5ApmtDm++N8z2MHzNPyxgFf\neZcf8wQ=\n-----END CERTIFICATE REQUEST-----\n";

    fn jwk(x: &str) -> Jwk {
        serde_json::from_value(json!({
            "kty": "OKP",
            "crv": "Ed25519",
            "alg": "EdDSA",
            "use": "sig",
            "kid": "kid_test",
            "x": x,
        }))
        .expect("test JWK should deserialize")
    }

    #[test]
    fn validates_ed25519_csr_and_jwk_binding() {
        let identity = validate_identity(
            "kid_test",
            &jwk("IbzL6hqCorbYKk2YQ28zG_CBTxGh0LxBkfQDpJvqnnE"),
            CSR,
            None,
        )
        .expect("valid identity should be accepted");
        assert_eq!(identity.public_key.len(), 32);
        assert!(!identity.jwk_thumbprint.is_empty());
    }

    #[test]
    fn rejects_csr_bound_to_another_key() {
        let error = validate_identity(
            "kid_test",
            &jwk("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
            CSR,
            None,
        )
        .expect_err("mismatched key should be rejected");
        assert!(
            error
                .to_string()
                .contains("does not match")
        );
    }

    #[test]
    fn validates_a_bounded_x25519_dispatch_key() {
        let key = DispatchEncryptionKey {
            key_id: "dispatch_test".into(),
            kem: DispatchKem::DhkemX25519HkdfSha256,
            public_key_base64url: "ERERERERERERERERERERERERERERERERERERERERERE".into(),
        };

        let validated = validate_dispatch_key(&key).expect("dispatch key should validate");

        assert_eq!(validated.public_key, [0x11; 32]);
        assert!(!validated.fingerprint.is_empty());
    }

    fn state_jwk() -> (serde_json::Value, [u8; 32]) {
        let public_key = SigningKey::from_bytes(&[42; 32])
            .verifying_key()
            .to_bytes();
        (
            json!({
                "kty": "OKP",
                "crv": "Ed25519",
                "alg": "EdDSA",
                "use": "sig",
                "kid": format!("agent_state_{:x}", Sha256::digest(public_key)),
                "x": URL_SAFE_NO_PAD.encode(public_key),
            }),
            public_key,
        )
    }

    #[test]
    fn state_signing_key_has_a_distinct_purpose_and_content_bound_identifier() {
        let (value, public_key) = state_jwk();
        let jwk = serde_json::from_value(value).unwrap();
        validate_state_signing_key(&jwk, &[1; 32]).unwrap();
        assert!(validate_state_signing_key(&jwk, &public_key).is_err());
    }

    #[test]
    fn rejects_invalid_state_key_descriptors() {
        for (field, replacement) in [
            ("kid", json!("state_arbitrary")),
            ("alg", json!("ES256")),
            ("use", json!("enc")),
            ("key_ops", json!(["sign"])),
            ("x", json!("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")),
            ("x", json!("invalid")),
            ("x", json!("ERERERERERERERERERERERERERERERERERERERERERERF")),
        ] {
            let (mut value, _) = state_jwk();
            value[field] = replacement;
            let key = serde_json::from_value(value).unwrap();
            assert!(validate_state_signing_key(&key, &[1; 32]).is_err(), "{field}");
        }
    }
}
