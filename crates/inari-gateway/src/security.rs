use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use jsonwebtoken::jwk::{
    AlgorithmParameters, EllipticCurve, Jwk, KeyAlgorithm, PublicKeyUse, ThumbprintHash,
};
use sha2::{Digest, Sha256};
use x509_parser::certification_request::X509CertificationRequest;
use x509_parser::extensions::{GeneralName, ParsedExtension};
use x509_parser::parse_x509_certificate;
use x509_parser::pem::parse_x509_pem;
use x509_parser::prelude::FromDer;
use x509_parser::x509::X509Name;

use crate::protocol::{AgentId, DispatchEncryptionKey, DispatchKem};
use crate::{GatewayError, GatewayResult};

mod state_envelopes;

pub use state_envelopes::UnverifiedAgentStateEnvelope;

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
    let key = state_verifying_key(jwk)?;
    if key.as_bytes() == identity_key {
        return Err(GatewayError::InvalidInput(
            "Agent State requires a separate signing key".into(),
        ));
    }
    Ok(())
}

fn state_verifying_key(jwk: &Jwk) -> GatewayResult<ed25519_dalek::VerifyingKey> {
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
    if verifying_key.is_weak() {
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
    Ok(verifying_key)
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
    agent_id: &AgentId,
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
    let verifying_key = ed25519_dalek::VerifyingKey::from_bytes(&public_key)
        .map_err(|_| GatewayError::InvalidInput("Ed25519 public key is invalid".into()))?;
    if verifying_key.is_weak() {
        return Err(GatewayError::InvalidInput("Ed25519 public key must not be weak".into()));
    }
    let digest = hex::encode(Sha256::digest(public_key));
    if agent_id.as_str() != format!("agt_{}", &digest[..24])
        || key_id != format!("kid_{}", &digest[..12])
    {
        return Err(GatewayError::InvalidInput(
            "Agent ID and key ID must identify the public JWK key".into(),
        ));
    }

    let (remaining, pem) = parse_x509_pem(csr_pem.as_bytes())
        .map_err(|_| GatewayError::InvalidInput("CSR is not valid PEM".into()))?;
    if remaining
        .iter()
        .any(|byte| !byte.is_ascii_whitespace())
    {
        return Err(GatewayError::InvalidInput("CSR must contain one PEM request".into()));
    }
    let (remaining, csr) = X509CertificationRequest::from_der(&pem.contents)
        .map_err(|_| GatewayError::InvalidInput("CSR is not valid PKCS#10 DER".into()))?;
    if !remaining.is_empty() {
        return Err(GatewayError::InvalidInput("CSR contains trailing DER data".into()));
    }
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
    validate_certificate_subject(&csr.certification_request_info.subject, agent_id)?;
    csr.certification_request_info
        .attributes_map()
        .map_err(|_| GatewayError::InvalidInput("CSR contains duplicate attributes".into()))?;
    validate_certificate_names(
        csr.requested_extensions()
            .into_iter()
            .flatten(),
        agent_id,
    )?;

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
        validate_certificate_subject(certificate.subject(), agent_id)?;
        validate_certificate_names(
            certificate
                .extensions()
                .iter()
                .map(|extension| extension.parsed_extension()),
            agent_id,
        )?;
    }

    Ok(ValidatedIdentity {
        key_id: key_id.into(),
        jwk_thumbprint: jwk.thumbprint(ThumbprintHash::SHA256),
        public_key,
        csr_fingerprint: URL_SAFE_NO_PAD.encode(Sha256::digest(&pem.contents)),
    })
}

fn validate_certificate_subject(subject: &X509Name<'_>, agent_id: &AgentId) -> GatewayResult<()> {
    if subject.iter_attributes().count() != 1
        || subject
            .iter_common_name()
            .next()
            .and_then(|name| name.as_str().ok())
            != Some(agent_id.as_str())
    {
        return Err(GatewayError::InvalidInput(
            "certificate subject must contain only the Agent ID common name".into(),
        ));
    }
    Ok(())
}

fn validate_certificate_names<'a>(
    extensions: impl IntoIterator<Item = &'a ParsedExtension<'a>>,
    agent_id: &AgentId,
) -> GatewayResult<()> {
    let expected = format!("urn:inari:{agent_id}");
    let mut sans = extensions
        .into_iter()
        .filter_map(|extension| {
            if let ParsedExtension::SubjectAlternativeName(names) = extension {
                Some(names)
            } else {
                None
            }
        });
    let valid = sans
        .next()
        .is_some_and(|names| names.general_names.as_slice() == [GeneralName::URI(&expected)])
        && sans.next().is_none();
    if !valid {
        return Err(GatewayError::InvalidInput(
            "certificate names must contain only the Agent Identity URI SAN".into(),
        ));
    }
    Ok(())
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

    const CSR: &str = include_str!("../tests/fixtures/enrollment/valid.csr.pem");

    fn identity_jwk(seed: u8) -> (crate::protocol::AgentId, String, Jwk) {
        let public = SigningKey::from_bytes(&[seed; 32])
            .verifying_key()
            .to_bytes();
        let digest = hex::encode(Sha256::digest(public));
        let agent_id = format!("agt_{}", &digest[..24])
            .parse()
            .unwrap();
        let key_id = format!("kid_{}", &digest[..12]);
        let jwk = serde_json::from_value(json!({
            "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
            "kid": key_id, "x": URL_SAFE_NO_PAD.encode(public),
        }))
        .unwrap();
        (agent_id, key_id, jwk)
    }

    #[test]
    fn validates_ed25519_csr_and_jwk_binding() {
        let (agent_id, key_id, jwk) = identity_jwk(1);
        let identity = validate_identity(&agent_id, &key_id, &jwk, CSR, None).unwrap();
        assert_eq!(identity.public_key.len(), 32);
        assert!(!identity.jwk_thumbprint.is_empty());
        let (_, pem) = x509_parser::pem::parse_x509_pem(CSR.as_bytes()).unwrap();
        assert_eq!(identity.csr_fingerprint, URL_SAFE_NO_PAD.encode(Sha256::digest(pem.contents)));
    }

    #[test]
    fn rejects_weak_transport_keys_before_reading_the_csr() {
        let (_, _, mut jwk) = identity_jwk(1);
        if let jsonwebtoken::jwk::AlgorithmParameters::OctetKeyPair(parameters) = &mut jwk.algorithm
        {
            parameters.x = URL_SAFE_NO_PAD.encode([0; 32]);
        }
        let digest = hex::encode(Sha256::digest([0; 32]));
        let key_id = format!("kid_{}", &digest[..12]);
        jwk.common.key_id = Some(key_id.clone());
        let agent_id = format!("agt_{}", &digest[..24])
            .parse()
            .unwrap();
        let error = validate_identity(&agent_id, &key_id, &jwk, "", None).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("must not be weak")
        );
    }

    #[test]
    fn rejects_csr_bound_to_another_key() {
        let (agent_id, key_id, jwk) = identity_jwk(2);
        let error = validate_identity(&agent_id, &key_id, &jwk, CSR, None).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("CSR public key does not match")
        );
    }

    #[test]
    fn rejects_claimed_agent_and_key_ids_for_another_identity() {
        let (agent_id, key_id, jwk) = identity_jwk(1);
        let other_agent = "agt_other".parse().unwrap();
        for (agent, kid) in [(&other_agent, key_id.as_str()), (&agent_id, "kid_other")] {
            let mut descriptor = jwk.clone();
            descriptor.common.key_id = Some(kid.into());
            let error = validate_identity(agent, kid, &descriptor, CSR, None).unwrap_err();
            assert!(
                error
                    .to_string()
                    .contains("must identify the public JWK key")
            );
        }
    }

    #[test]
    fn rejects_signed_csrs_with_other_subjects_or_names() {
        let (agent_id, key_id, jwk) = identity_jwk(1);
        for request in [
            include_str!("../tests/fixtures/enrollment/wrong-subject.csr.pem"),
            include_str!("../tests/fixtures/enrollment/extra-subject.csr.pem"),
            include_str!("../tests/fixtures/enrollment/missing-san.csr.pem"),
            include_str!("../tests/fixtures/enrollment/wrong-san.csr.pem"),
            include_str!("../tests/fixtures/enrollment/dns-san.csr.pem"),
            include_str!("../tests/fixtures/enrollment/extra-san.csr.pem"),
            include_str!("../tests/fixtures/enrollment/duplicate-san.csr.pem"),
        ] {
            let error = validate_identity(&agent_id, &key_id, &jwk, request, None).unwrap_err();
            assert!(
                error
                    .to_string()
                    .contains("certificate")
            );
        }
    }

    #[test]
    fn validates_existing_certificate_identity() {
        let (agent_id, key_id, jwk) = identity_jwk(1);
        validate_identity(
            &agent_id,
            &key_id,
            &jwk,
            CSR,
            Some(include_str!("../tests/fixtures/enrollment/valid.cert.pem")),
        )
        .unwrap();
        for certificate in [
            include_str!("../tests/fixtures/enrollment/wrong-subject.cert.pem"),
            include_str!("../tests/fixtures/enrollment/wrong-san.cert.pem"),
        ] {
            let error =
                validate_identity(&agent_id, &key_id, &jwk, CSR, Some(certificate)).unwrap_err();
            assert!(
                error
                    .to_string()
                    .contains("certificate")
            );
        }
    }

    #[test]
    fn rejects_multiple_pem_requests() {
        let (agent_id, key_id, jwk) = identity_jwk(1);
        let error =
            validate_identity(&agent_id, &key_id, &jwk, &format!("{CSR}{CSR}"), None).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("one PEM request")
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
