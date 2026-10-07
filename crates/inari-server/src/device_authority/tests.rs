use serde_json::{Value, json};

use super::*;

fn fixture() -> (AuthorityDraft, AuthorityApproval, AuthorityTime) {
    let vectors: Value = serde_json::from_str(include_str!(
        "../../../../contracts/device-authority.test-vectors.json"
    ))
    .unwrap();
    let case = &vectors["cases"][0];
    let bundle: AuthorityBundle = serde_json::from_value(case["activated_bundle"].clone()).unwrap();
    let signer = |purpose| {
        bundle
            .manifest
            .signers
            .iter()
            .find(|signer| signer.purpose == purpose)
            .unwrap()
            .clone()
    };
    let key = |signer: SignerRecord| TransitApproval {
        key_name: serde_json::to_value(signer.purpose)
            .unwrap()
            .as_str()
            .unwrap()
            .replace('_', "-"),
        key_version: NonZeroU32::new(1).unwrap(),
        signer,
    };
    let approval = AuthorityApproval {
        agent_id: bundle.manifest.agent_id.clone(),
        scope: bundle.manifest.scope.clone(),
        transit_mount: "transit".into(),
        root: key(serde_json::from_value(case["trusted_signer"].clone()).unwrap()),
        profile: key(signer(SignerPurpose::DriverProfile)),
        matrix: key(signer(SignerPurpose::CertificationMatrix)),
        binding: key(signer(SignerPurpose::BindingRevision)),
        agent_signers: bundle
            .manifest
            .signers
            .iter()
            .filter(|item| {
                matches!(
                    item.purpose,
                    SignerPurpose::DeviceObservation | SignerPurpose::DeviceTestEvidence
                )
            })
            .cloned()
            .collect(),
    };
    let draft = AuthorityDraft {
        agent_id: bundle.manifest.agent_id,
        scope: bundle.manifest.scope,
        revision_id: bundle.revision.revision.revision_id,
        revision_number: bundle.revision.revision.revision_number,
        effective_at: bundle.revision.revision.effective_at,
        expires_at: bundle
            .revision
            .revision
            .expires_at
            .unwrap(),
        profiles: bundle
            .manifest
            .profiles
            .into_iter()
            .map(|item| item.profile)
            .collect(),
        certification_rows: bundle
            .manifest
            .certification_rows
            .into_iter()
            .map(|item| item.row)
            .collect(),
        bindings: bundle
            .manifest
            .bindings
            .into_iter()
            .map(|item| item.revision)
            .collect(),
        evidence: bundle.manifest.evidence,
        activations: bundle.manifest.activations,
    };
    (draft, approval, serde_json::from_value(case["now"].clone()).unwrap())
}

#[test]
fn preparation_preserves_agent_signed_evidence() {
    let (draft, approval, now) = fixture();
    let evidence = draft.evidence.clone();
    let prepared = prepare(draft, &approval, now).unwrap();
    assert_eq!(prepared.manifest.evidence, evidence);
    assert_eq!(prepared.manifest.signers.len(), approval.agent_signers.len() + 3);
    assert_eq!(prepared.manifest.profiles[0].signer_key_id, approval.profile.signer.key_id);
}

#[test]
fn wrong_target_expiry_and_key_purpose_fail_before_signing() {
    let (mut draft, approval, now) = fixture();
    draft.agent_id = Identifier::new("another-agent".into()).unwrap();
    assert!(prepare(draft, &approval, now).is_err());
    let (mut draft, approval, now) = fixture();
    draft.expires_at = now;
    assert!(prepare(draft, &approval, now).is_err());
    let (draft, mut approval, now) = fixture();
    approval.profile.signer.purpose = SignerPurpose::DeviceTestEvidence;
    assert!(prepare(draft, &approval, now).is_err());
    let (mut draft, approval, now) = fixture();
    draft.scope.site_id = Identifier::new("another-site".into()).unwrap();
    assert!(prepare(draft, &approval, now).is_err());
}

#[test]
fn forged_or_unapproved_evidence_fails_before_signing() {
    let (mut draft, approval, now) = fixture();
    draft.evidence[0].signature = HexBytes::new([0; 64]);
    assert!(prepare(draft, &approval, now).is_err());
    let (draft, mut approval, now) = fixture();
    approval.agent_signers.clear();
    assert!(prepare(draft, &approval, now).is_err());
}

#[test]
fn document_bounds_and_output_never_replace_existing_files() {
    let directory = tempfile::tempdir().unwrap();
    let input = directory.path().join("input.json");
    std::fs::write(&input, vec![b' '; MAX_DOCUMENT_BYTES + 1]).unwrap();
    assert!(read_document::<AuthorityDraft>(&input).is_err());
    std::fs::write(&input, serde_json::to_vec(&json!({"unknown": true})).unwrap()).unwrap();
    assert!(read_document::<AuthorityDraft>(&input).is_err());
    let (draft, approval, now) = fixture();
    let bundle = prepare(draft, &approval, now).unwrap();
    let output = directory.path().join("bundle.json");
    std::fs::write(&output, b"keep this file").unwrap();
    assert!(write_bundle(&output, &bundle).is_err());
    assert_eq!(std::fs::read(&output).unwrap(), b"keep this file");
}

#[tokio::test]
async fn signed_bundle_preserves_agent_evidence_and_verifies_the_activation_graph() {
    use axum::extract::{Path, State};
    use axum::routing::{get, post};
    use axum::{Json, Router};
    use base64::Engine;
    use base64::engine::general_purpose::STANDARD;
    use ed25519_dalek::{Signer, SigningKey};
    use std::collections::BTreeMap;

    let (draft, mut approval, now) = fixture();
    let evidence = draft.evidence.clone();
    let keys: BTreeMap<_, _> =
        [&mut approval.root, &mut approval.profile, &mut approval.matrix, &mut approval.binding]
            .into_iter()
            .enumerate()
            .map(|(index, approved)| {
                let key = SigningKey::from_bytes(&[40 + index as u8; 32]);
                approved.signer.public_key = HexBytes::new(key.verifying_key().to_bytes());
                (approved.key_name.clone(), key)
            })
            .collect();
    let router = Router::new()
        .route(
            "/v1/auth/kubernetes/login",
            post(|Json(body): Json<Value>| async move {
                assert_eq!(body, json!({"role":"operator", "jwt":"test-workload-jwt"}));
                Json(json!({"auth":{"client_token":"test-token", "lease_duration":60}}))
            }),
        )
        .route(
            "/v1/transit/keys/{name}",
            get(|State(keys): State<Arc<BTreeMap<String, SigningKey>>>, Path(name): Path<String>| async move {
                let key = keys.get(&name).unwrap();
                Json(json!({"data": {
                    "name": name, "type":"ed25519", "derived":false,
                    "exportable":false, "allow_plaintext_backup":false,
                    "supports_signing":true, "min_encryption_version":1,
                    "keys":{"1":{"public_key":STANDARD.encode(key.verifying_key().as_bytes())}}
                }}))
            }),
        )
        .route(
            "/v1/transit/sign/{name}",
            post(|State(keys): State<Arc<BTreeMap<String, SigningKey>>>, Path(name): Path<String>, Json(body): Json<Value>| async move {
                assert_eq!(body["key_version"], 1);
                assert_eq!(body["prehashed"], false);
                let bytes = STANDARD.decode(body["input"].as_str().unwrap()).unwrap();
                let signature = keys.get(&name).unwrap().sign(&bytes);
                Json(json!({"data":{"signature":format!("vault:v1:{}", STANDARD.encode(signature.to_bytes()))}}))
            }),
        )
        .with_state(Arc::new(keys));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .unwrap();
    let address = format!("http://{}/", listener.local_addr().unwrap())
        .parse()
        .unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, router)
            .await
            .unwrap()
    });
    let directory = tempfile::tempdir().unwrap();
    let token = directory.path().join("token");
    std::fs::write(&token, "test-workload-jwt").unwrap();
    let client = Arc::new(
        OpenBaoClient::load_test(OpenBaoConfig {
            address: Some(address),
            kubernetes_role: Some("operator".into()),
            service_account_token_file: token,
            ..Default::default()
        })
        .await
        .unwrap(),
    );
    let signed = sign_bundle(draft, &approval, client, now).await;
    server.abort();
    let signed = signed.unwrap();
    assert_eq!(signed.manifest.evidence, evidence);
    assert!(!signed.manifest.activations.is_empty());
    signed
        .verify(&approval.root.signer, approval.agent_id.as_str(), &approval.scope, now)
        .unwrap();
}
