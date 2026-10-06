use ed25519_dalek::{Signer, SigningKey};
use inari_gateway::device_authority::{
    AuthorityBundle, AuthorityContract, AuthorityDigest, AuthorityScope, AuthorityTime,
    CanonicalRecord, DriverProfile, HexBytes, Identifier, PositiveInteger, RecordPayload,
    SignedAuthorityRevision, SignedBindingRevision, SignedDeviceObservation,
    SignedDeviceTestEvidence, SignedDriverProfile, SignedHardwareCertificationMatrixRow,
    SignerRecord, SignerState,
};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
struct Vectors {
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    microsecond: u32,
    now: AuthorityTime,
    agent_id: String,
    scope: AuthorityScope,
    trusted_signer: SignerRecord,
    initial_bundle: Value,
    activated_bundle: Value,
    records: Vec<Record>,
}

#[derive(Deserialize)]
struct Record {
    purpose: String,
    record: Value,
    signer: SignerRecord,
    canonical: String,
}

fn vectors() -> Vectors {
    serde_json::from_str(include_str!("../../../contracts/device-authority.test-vectors.json"))
        .expect("Agent-generated conformance vectors")
}

fn compare_payload<T: RecordPayload>(payload: T, record: &Record) {
    let canonical = CanonicalRecord::new(payload).expect("validated record");
    assert_eq!(canonical.as_bytes(), record.canonical.as_bytes(), "{}", record.purpose);
    assert_eq!(
        canonical.digest().as_str(),
        record.record["digest"]
            .as_str()
            .unwrap()
    );
}

fn fixture_signature<T: RecordPayload>(canonical: &CanonicalRecord<T>) -> HexBytes<64> {
    let purpose = serde_json::to_value(T::PURPOSE).unwrap();
    let seed =
        Sha256::digest(format!("Inari cross-language fixture key: {}", purpose.as_str().unwrap()));
    let key = SigningKey::from_bytes(&seed.into());
    HexBytes::new(
        key.sign(canonical.as_bytes())
            .to_bytes(),
    )
}

fn sign_fixture_manifest(bundle: &mut AuthorityBundle) {
    bundle.revision.revision.manifest_digest =
        AuthorityDigest::of(&serde_json_canonicalizer::to_vec(&bundle.manifest).unwrap());
    let canonical = CanonicalRecord::new(bundle.revision.revision.clone()).unwrap();
    let signature = fixture_signature(&canonical);
    bundle.revision = SignedAuthorityRevision::from_signature(
        canonical,
        bundle.revision.signer_key_id.clone(),
        signature,
    );
}

#[test]
fn every_signature_purpose_matches_the_agent_bytes_and_digest() {
    for case in vectors().cases {
        for record in case.records {
            macro_rules! compare {
                ($signed:ty, $field:ident) => {{
                    let signed: $signed = serde_json::from_value(record.record.clone()).unwrap();
                    signed
                        .verify(&record.signer, case.now)
                        .unwrap();
                    assert_eq!(serde_json::to_value(&signed).unwrap(), record.record);
                    compare_payload(signed.$field, &record);
                }};
            }
            match record.purpose.as_str() {
                "authority_revision" => compare!(SignedAuthorityRevision, revision),
                "driver_profile" => compare!(SignedDriverProfile, profile),
                "certification_matrix" => compare!(SignedHardwareCertificationMatrixRow, row),
                "binding_revision" => compare!(SignedBindingRevision, revision),
                "device_test_evidence" => compare!(SignedDeviceTestEvidence, evidence),
                "device_observation" => compare!(SignedDeviceObservation, observation),
                other => panic!("unknown vector purpose: {other}"),
            }
        }
    }
}

#[test]
fn initial_and_activated_bundles_verify_without_changing_the_wire() {
    for case in vectors().cases {
        for wire in [case.initial_bundle, case.activated_bundle] {
            let bundle: AuthorityBundle = serde_json::from_value(wire.clone()).unwrap();
            assert_eq!(bundle.manifest.contract, AuthorityContract::V1);
            bundle
                .verify(&case.trusted_signer, &case.agent_id, &case.scope, case.now)
                .unwrap();
            assert_eq!(serde_json::to_value(&bundle).unwrap(), wire, "{}", case.microsecond);
        }
    }
}

#[test]
fn bundle_rejects_another_target_and_expired_or_retired_trust() {
    let case = vectors().cases.remove(0);
    let bundle: AuthorityBundle = serde_json::from_value(case.initial_bundle).unwrap();
    assert!(
        bundle
            .verify(&case.trusted_signer, "another-agent", &case.scope, case.now)
            .is_err()
    );
    let mut scope = case.scope.clone();
    scope.site_id = Identifier::new("another-site".into()).unwrap();
    assert!(
        bundle
            .verify(&case.trusted_signer, &case.agent_id, &scope, case.now)
            .is_err()
    );
    let mut signer = case.trusted_signer.clone();
    signer.state = SignerState::Retired;
    signer.retired_at = Some(case.now);
    assert!(
        bundle
            .verify(&signer, &case.agent_id, &case.scope, case.now)
            .is_err()
    );
    let expires = bundle
        .revision
        .revision
        .expires_at
        .unwrap();
    assert!(
        bundle
            .verify(&case.trusted_signer, &case.agent_id, &case.scope, expires)
            .is_err()
    );
}

#[test]
fn signature_rejects_changed_payload_digest_key_purpose_and_lifetime() {
    let case = vectors().cases.remove(0);
    let vector = &case.records[1];
    let signed: SignedDriverProfile = serde_json::from_value(vector.record.clone()).unwrap();
    let mut changed = signed.clone();
    changed.profile.driver_id = Identifier::new("another-driver".into()).unwrap();
    assert!(
        changed
            .verify(&vector.signer, case.now)
            .is_err()
    );
    let mut signer = vector.signer.clone();
    signer.purpose = case.trusted_signer.purpose;
    assert!(
        signed
            .verify(&signer, case.now)
            .is_err()
    );
    signer = vector.signer.clone();
    signer.key_id = Identifier::new("another-key".into()).unwrap();
    assert!(
        signed
            .verify(&signer, case.now)
            .is_err()
    );
    signer = vector.signer.clone();
    signer.public_key = case.trusted_signer.public_key;
    assert!(
        signed
            .verify(&signer, case.now)
            .is_err()
    );
    assert!(
        signed
            .verify(&vector.signer, vector.signer.not_after.unwrap())
            .is_err()
    );
    let mut wire = vector.record.clone();
    wire["digest"] = json!("0".repeat(64));
    let changed: SignedDriverProfile = serde_json::from_value(wire).unwrap();
    assert!(
        changed
            .verify(&vector.signer, case.now)
            .is_err()
    );
}

#[test]
fn syntax_rejects_unknown_fields_missing_nulls_bad_hex_and_inexact_numbers() {
    let case = vectors().cases.remove(0);
    let mut wire = case.records[1].record.clone();
    wire["profile"]["unapproved_field"] = json!(true);
    assert!(serde_json::from_value::<SignedDriverProfile>(wire).is_err());
    let mut wire = case.records[1].record.clone();
    wire["profile"]
        .as_object_mut()
        .unwrap()
        .remove("expires_at");
    assert!(serde_json::from_value::<SignedDriverProfile>(wire).is_err());
    let mut wire = case.records[1].record.clone();
    wire["signature"] = json!("00");
    assert!(serde_json::from_value::<SignedDriverProfile>(wire).is_err());
    assert!(PositiveInteger::new(0).is_err());
    assert!(PositiveInteger::new(9_007_199_254_740_992).is_err());
    assert!(serde_json::from_str::<PositiveInteger>("true").is_err());
    assert!(serde_json::from_str::<AuthorityTime>("\"2026-08-28T12:00:00+01:00\"").is_err());
    assert!(serde_json::from_str::<AuthorityTime>("\"2026-08-28T12:00:00.000000001Z\"").is_err());
}

#[test]
fn canonical_record_rejects_bad_lifetimes_and_repeated_capabilities() {
    let case = vectors().cases.remove(0);
    let mut profile: DriverProfile =
        serde_json::from_value(case.records[1].record["profile"].clone()).unwrap();
    profile.expires_at = Some(profile.effective_at);
    assert!(CanonicalRecord::new(profile).is_err());
    let mut profile: DriverProfile =
        serde_json::from_value(case.records[1].record["profile"].clone()).unwrap();
    profile
        .capabilities
        .push(profile.capabilities[0].clone());
    assert!(CanonicalRecord::new(profile).is_err());
}

#[test]
fn record_acceptance_matches_agent_text_and_capability_bounds() {
    assert!(Identifier::new(" ".into()).is_ok());
    let case = vectors().cases.remove(0);
    let mut observation = case.records[5].record.clone();
    observation["observation"]["reason"] = json!("");
    let observation: SignedDeviceObservation = serde_json::from_value(observation).unwrap();
    assert!(CanonicalRecord::new(observation.observation).is_ok());
    let mut profile: DriverProfile =
        serde_json::from_value(case.records[1].record["profile"].clone()).unwrap();
    let capability = profile.capabilities[0].clone();
    profile.capabilities = (0..257)
        .map(|number| {
            let mut capability = capability.clone();
            capability.capability_id = Identifier::new(format!("capability-{number}")).unwrap();
            capability
        })
        .collect();
    assert!(CanonicalRecord::new(profile).is_ok());
}

#[test]
fn correctly_signed_activation_rejects_a_changed_device_graph() {
    let case = vectors().cases.remove(0);
    let mut bundle: AuthorityBundle = serde_json::from_value(case.activated_bundle).unwrap();
    let binding = &mut bundle.manifest.bindings[0];
    binding.revision.device_identity_digest = AuthorityDigest::of(b"another Device identity");
    let canonical = CanonicalRecord::new(binding.revision.clone()).unwrap();
    let signature = fixture_signature(&canonical);
    *binding =
        SignedBindingRevision::from_signature(canonical, binding.signer_key_id.clone(), signature);
    sign_fixture_manifest(&mut bundle);
    let error = bundle
        .verify(&case.trusted_signer, &case.agent_id, &case.scope, case.now)
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("active Binding Revision graph differs")
    );
}

#[test]
fn correctly_signed_manifest_cannot_replace_its_root_or_repeat_records() {
    let case = vectors().cases.remove(0);
    let original: AuthorityBundle = serde_json::from_value(case.initial_bundle).unwrap();
    let mut bundle = original.clone();
    bundle
        .manifest
        .signers
        .push(case.trusted_signer.clone());
    sign_fixture_manifest(&mut bundle);
    let error = bundle
        .verify(&case.trusted_signer, &case.agent_id, &case.scope, case.now)
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("cannot replace the authority trust key")
    );

    let mut bundle = original;
    bundle
        .manifest
        .bindings
        .push(bundle.manifest.bindings[0].clone());
    sign_fixture_manifest(&mut bundle);
    let error = bundle
        .verify(&case.trusted_signer, &case.agent_id, &case.scope, case.now)
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("manifest repeats a record")
    );
}

#[test]
fn correctly_signed_activation_requires_passed_evidence() {
    let case = vectors().cases.remove(0);
    let mut bundle: AuthorityBundle = serde_json::from_value(case.activated_bundle).unwrap();
    let evidence = &mut bundle.manifest.evidence[0];
    evidence.evidence.result = inari_gateway::device_authority::DeviceTestResult::FailedEnvironment;
    let canonical = CanonicalRecord::new(evidence.evidence.clone()).unwrap();
    let signature = fixture_signature(&canonical);
    *evidence = SignedDeviceTestEvidence::from_signature(
        canonical,
        evidence.signer_key_id.clone(),
        signature,
    );
    sign_fixture_manifest(&mut bundle);
    let error = bundle
        .verify(&case.trusted_signer, &case.agent_id, &case.scope, case.now)
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("active Binding Revision graph differs")
    );
}
