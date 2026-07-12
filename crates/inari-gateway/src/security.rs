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

use crate::{GatewayError, GatewayResult};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedIdentity {
    pub key_id: String,
    pub jwk_thumbprint: String,
    pub public_key: [u8; 32],
    pub csr_fingerprint: String,
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
    use jsonwebtoken::jwk::Jwk;
    use serde_json::json;

    use super::validate_identity;

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
}
