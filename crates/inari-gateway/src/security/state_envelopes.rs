use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use ed25519_dalek::Signature;
use jsonwebtoken::jwk::Jwk;
use serde::Deserialize;

use crate::protocol::{AgentStateObservation, PrintJobOutputEvidence, PrintJobState};
use crate::{GatewayError, GatewayResult};

const MAX_JSON_INTEGER: u64 = (1 << 53) - 1;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProtectedHeader {
    alg: String,
    kid: String,
    typ: String,
}

/// Keeps claims inaccessible until the attached signature and contract pass verification.
pub struct UnverifiedAgentStateEnvelope<'a> {
    signing_input: &'a str,
    header: ProtectedHeader,
    payload: Vec<u8>,
    signature: Signature,
}

impl<'a> UnverifiedAgentStateEnvelope<'a> {
    pub fn parse(compact: &'a str) -> GatewayResult<Self> {
        if compact.len() > 65_536 {
            return Err(invalid("envelope exceeds its size limit"));
        }
        let mut parts = compact.split('.');
        let (Some(protected), Some(payload), Some(signature), None) =
            (parts.next(), parts.next(), parts.next(), parts.next())
        else {
            return Err(invalid("envelope must use attached compact JWS"));
        };
        if protected.len() > 1024 {
            return Err(invalid("protected header exceeds its size limit"));
        }
        let protected = decode(protected)?;
        let header: ProtectedHeader = serde_json::from_slice(&protected)
            .map_err(|_| invalid("protected header is invalid"))?;
        if header.alg != "EdDSA"
            || header.typ != "application/inari-agent-state+jws"
            || !identifier(&header.kid)
        {
            return Err(invalid("protected header has an unsupported algorithm, type, or key ID"));
        }
        let payload = decode(payload)?;
        let signature = Signature::from_slice(&decode(signature)?)
            .map_err(|_| invalid("signature must contain 64 bytes"))?;
        let signing_input = compact
            .rsplit_once('.')
            .ok_or_else(|| invalid("signature is missing"))?
            .0;
        Ok(Self { signing_input, header, payload, signature })
    }

    #[must_use]
    pub fn key_id(&self) -> &str {
        &self.header.kid
    }

    /// The caller must resolve the key within the authenticated Agent's State key registry.
    pub fn verify(self, registered_key: &Jwk) -> GatewayResult<AgentStateObservation> {
        if registered_key.common.key_id.as_deref() != Some(self.key_id()) {
            return Err(invalid("key ID does not match its registered signing key"));
        }
        super::state_verifying_key(registered_key)?
            .verify_strict(self.signing_input.as_bytes(), &self.signature)
            .map_err(|_| invalid("signature is invalid"))?;
        let value: serde_json::Value = serde_json::from_slice(&self.payload)
            .map_err(|_| invalid("claims are not valid JSON"))?;
        if serde_json_canonicalizer::to_vec(&value)? != self.payload {
            return Err(invalid("claims must use RFC 8785 canonical JSON"));
        }
        let observation: AgentStateObservation = serde_json::from_value(value)
            .map_err(|_| invalid("claims do not match the managed Print Job contract"))?;
        validate_observation(&observation)?;
        Ok(observation)
    }
}

fn validate_observation(observation: &AgentStateObservation) -> GatewayResult<()> {
    let job = &observation.job;
    if observation.contract_major != 1 || job.contract_version != "v1" {
        return Err(invalid("contract version is unsupported"));
    }
    for value in [
        &observation.envelope_id,
        &observation.agent_boot_id,
        &observation.reconciliation_session_id,
        &job.print_job_id,
        &job.origin.database,
        &job.origin.company_id,
        &job.origin.report_binding_id,
        &job.origin.report_action,
        &job.origin.source_model,
    ] {
        if !identifier(value) {
            return Err(invalid("claims contain an invalid identifier"));
        }
    }
    if [
        observation.dispatch_epoch,
        observation.envelope_sequence,
        observation.durable_state_sequence,
        job.state_version,
    ]
    .iter()
    .any(|value| !(1..=MAX_JSON_INTEGER).contains(value))
    {
        return Err(invalid("counters must be positive JSON-safe integers"));
    }
    if !fingerprint(&observation.payload_fingerprint) {
        return Err(invalid("Payload Fingerprint is invalid"));
    }
    let records = &job.origin.record_ids;
    let wizard = &job.origin.wizard_input_digest;
    if records.is_empty() == wizard.is_none()
        || records.len() > 100
        || records.iter().any(|record| {
            !record
                .parse::<i64>()
                .is_ok_and(|value| value > 0 && value.to_string() == *record)
        })
        || wizard
            .as_ref()
            .is_some_and(|value| !fingerprint(value))
    {
        return Err(invalid("Report Print Origin source is invalid"));
    }
    let latest_at = job
        .terminal_at
        .or(job.started_at)
        .unwrap_or(job.accepted_at);
    if job.expires_at <= job.accepted_at
        || job
            .started_at
            .is_some_and(|at| at < job.accepted_at)
        || job.terminal_at.is_some_and(|at| {
            at < job
                .started_at
                .unwrap_or(job.accepted_at)
        })
        || observation.observed_at < latest_at
        || observation.issued_at < observation.observed_at
    {
        return Err(invalid("timestamps conflict with the Print Job lifecycle"));
    }
    let valid_lifecycle = match job.state {
        PrintJobState::Accepted => {
            job.state_version == 1 && job.started_at.is_none() && job.terminal_at.is_none()
        },
        PrintJobState::InProgress => {
            job.state_version >= 2 && job.started_at.is_some() && job.terminal_at.is_none()
        },
        PrintJobState::OutputConfirmed => {
            job.state_version >= 3
                && job.started_at.is_some()
                && job.terminal_at.is_some()
                && job.confirmation_evidence == Some(PrintJobOutputEvidence::Device)
        },
        PrintJobState::OutcomeUnknown => {
            job.state_version >= 3 && job.started_at.is_some() && job.terminal_at.is_some()
        },
        PrintJobState::Failed => job.state_version >= 2 && job.terminal_at.is_some(),
        PrintJobState::Expired | PrintJobState::Canceled => {
            job.state_version >= 2 && job.started_at.is_none() && job.terminal_at.is_some()
        },
    };
    if !valid_lifecycle
        || (job.state != PrintJobState::OutputConfirmed && job.confirmation_evidence.is_some())
    {
        return Err(invalid("state conflicts with the Print Job lifecycle or Output Evidence"));
    }
    if matches!(job.state, PrintJobState::Failed | PrintJobState::OutcomeUnknown) {
        if !job
            .error_code
            .as_deref()
            .is_some_and(|value| safe_code(value, 64, false))
            || !job
                .message_key
                .as_deref()
                .is_some_and(|value| safe_code(value, 128, true))
        {
            return Err(invalid("failure requires a safe error code and message key"));
        }
    } else if job.error_code.is_some() || job.message_key.is_some() {
        return Err(invalid("state cannot contain an error"));
    }
    Ok(())
}

fn decode(value: &str) -> GatewayResult<Vec<u8>> {
    URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| invalid("JWS segments must use canonical base64url"))
}

fn identifier(value: &str) -> bool {
    value.len() <= 256
        && value
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:/-".contains(&byte))
}

fn fingerprint(value: &str) -> bool {
    value
        .strip_prefix("sha256:")
        .is_some_and(|digest| {
            digest.len() == 64
                && digest
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
}

fn safe_code(value: &str, limit: usize, dots: bool) -> bool {
    value.len() <= limit
        && value
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_lowercase)
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || byte == b'_'
                || (dots && byte == b'.')
        })
}

fn invalid(detail: &str) -> GatewayError {
    GatewayError::InvalidInput(format!("Agent State {detail}"))
}

#[cfg(test)]
mod tests {
    use base64::Engine;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use ed25519_dalek::{Signer, SigningKey};
    use jsonwebtoken::jwk::Jwk;
    use serde::Deserialize;
    use serde_json::{Value, json};

    use super::UnverifiedAgentStateEnvelope;

    #[derive(Deserialize)]
    struct Fixture {
        public_jwk: Jwk,
        claims: Value,
        state_envelope: String,
    }

    fn fixture() -> Fixture {
        serde_json::from_str(include_str!("../../tests/fixtures/agent-state-observation.json"))
            .unwrap()
    }

    fn header(key: &Jwk) -> Value {
        json!({"alg": "EdDSA", "kid": key.common.key_id, "typ": "application/inari-agent-state+jws"})
    }

    fn sign(header: &Value, payload: &[u8]) -> String {
        let protected = URL_SAFE_NO_PAD.encode(serde_json_canonicalizer::to_vec(header).unwrap());
        let payload = URL_SAFE_NO_PAD.encode(payload);
        let signing_input = format!("{protected}.{payload}");
        let signature = SigningKey::from_bytes(&[42; 32]).sign(signing_input.as_bytes());
        format!("{signing_input}.{}", URL_SAFE_NO_PAD.encode(signature.to_bytes()))
    }

    #[test]
    fn verifies_the_python_agents_canonical_state_envelope() {
        let fixture = fixture();
        let parsed = UnverifiedAgentStateEnvelope::parse(&fixture.state_envelope).unwrap();
        assert_eq!(
            Some(parsed.key_id()),
            fixture
                .public_jwk
                .common
                .key_id
                .as_deref()
        );
        let observation = parsed
            .verify(&fixture.public_jwk)
            .unwrap();
        assert_eq!(serde_json::to_value(observation).unwrap(), fixture.claims);
    }

    #[test]
    fn rejects_tampered_payloads_and_wrong_registered_keys() {
        let fixture = fixture();
        let mut compact = fixture.state_envelope.clone();
        let start = compact.find('.').unwrap() + 10;
        let replacement = if &compact[start..start + 1] == "A" { "B" } else { "A" };
        compact.replace_range(start..start + 1, replacement);
        assert!(
            UnverifiedAgentStateEnvelope::parse(&compact)
                .unwrap()
                .verify(&fixture.public_jwk)
                .is_err()
        );
        let mut other = fixture.public_jwk.clone();
        other.common.key_id = Some("other-key".into());
        assert!(
            UnverifiedAgentStateEnvelope::parse(&fixture.state_envelope)
                .unwrap()
                .verify(&other)
                .is_err()
        );
        let mut other: Value = serde_json::to_value(&fixture.public_jwk).unwrap();
        other["use"] = json!("enc");
        let other = serde_json::from_value(other).unwrap();
        assert!(
            UnverifiedAgentStateEnvelope::parse(&fixture.state_envelope)
                .unwrap()
                .verify(&other)
                .is_err()
        );
    }

    #[test]
    fn rejects_noncanonical_and_duplicate_json_claims() {
        let fixture = fixture();
        let canonical = serde_json_canonicalizer::to_string(&fixture.claims).unwrap();
        for payload in [
            serde_json::to_string_pretty(&fixture.claims).unwrap(),
            format!("{{\"contract_major\":1,{}", &canonical[1..]),
        ] {
            let compact = sign(&header(&fixture.public_jwk), payload.as_bytes());
            assert!(
                UnverifiedAgentStateEnvelope::parse(&compact)
                    .unwrap()
                    .verify(&fixture.public_jwk)
                    .is_err()
            );
        }
    }

    #[test]
    fn rejects_unsupported_jws_headers_and_segments() {
        let fixture = fixture();
        for (field, value) in [
            ("alg", json!("none")),
            ("typ", json!("JWT")),
            ("crit", json!(["extension"])),
            ("b64", json!(false)),
            ("jwk", json!({})),
        ] {
            let mut header = header(&fixture.public_jwk);
            header[field] = value;
            let compact =
                sign(&header, &serde_json_canonicalizer::to_vec(&fixture.claims).unwrap());
            assert!(UnverifiedAgentStateEnvelope::parse(&compact).is_err(), "{field}");
        }
        for compact in ["", "a.b", "a.b.c.d", "e30=.e30=.AA=="] {
            assert!(UnverifiedAgentStateEnvelope::parse(compact).is_err());
        }
        assert!(UnverifiedAgentStateEnvelope::parse(&"a".repeat(65_537)).is_err());
    }

    #[test]
    fn rejects_signed_claims_that_violate_the_print_job_contract() {
        let fixture = fixture();
        for (path, replacement) in [
            ("/contract_major", json!(2)),
            ("/dispatch_epoch", json!(0)),
            ("/envelope_sequence", json!(true)),
            ("/durable_state_sequence", json!(9_007_199_254_740_992_u64)),
            ("/agent_boot_id", json!("invalid space")),
            ("/payload_fingerprint", json!("sha256:FFFF")),
            ("/job/state_version", json!(2)),
            ("/job/state", json!("output_confirmed")),
            ("/job/error_code", json!("device_error")),
            ("/job/expires_at", json!("2026-01-01T00:00:00Z")),
            ("/issued_at", json!("2026-01-01T00:00:00Z")),
            ("/job/origin/record_ids", json!([])),
            ("/job/origin/record_ids", json!(["-1"])),
            ("/job/origin/wizard_input_digest", json!(format!("sha256:{}", "a".repeat(64)))),
        ] {
            let mut claims = fixture.claims.clone();
            *claims.pointer_mut(path).unwrap() = replacement;
            let compact = sign(
                &header(&fixture.public_jwk),
                &serde_json_canonicalizer::to_vec(&claims).unwrap(),
            );
            assert!(
                UnverifiedAgentStateEnvelope::parse(&compact)
                    .unwrap()
                    .verify(&fixture.public_jwk)
                    .is_err(),
                "{path}"
            );
        }
    }

    #[test]
    fn accepts_confirmed_output_only_with_device_evidence() {
        let fixture = fixture();
        for (evidence, expected) in [("device", true), ("transport", false), ("spooler", false)] {
            let mut claims = fixture.claims.clone();
            claims["job"]["state"] = json!("output_confirmed");
            claims["job"]["state_version"] = json!(3);
            claims["job"]["started_at"] = claims["job"]["accepted_at"].clone();
            claims["job"]["terminal_at"] = claims["job"]["accepted_at"].clone();
            claims["job"]["confirmation_evidence"] = json!(evidence);
            let compact = sign(
                &header(&fixture.public_jwk),
                &serde_json_canonicalizer::to_vec(&claims).unwrap(),
            );
            assert_eq!(
                UnverifiedAgentStateEnvelope::parse(&compact)
                    .unwrap()
                    .verify(&fixture.public_jwk)
                    .is_ok(),
                expected
            );
        }
    }
}
