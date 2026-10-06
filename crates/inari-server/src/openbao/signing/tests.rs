use std::num::NonZeroU32;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use axum::extract::State;
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::{Signer, SigningKey};
use inari_gateway::device_authority::{
    AuthorityTime, CanonicalRecord, DriverProfile, HexBytes, SignerPurpose, SignerRecord,
    SignerState,
};
use serde_json::{Value, json};
use tokio::task::JoinHandle;

use super::AuthoritySigningKey;
use crate::config::OpenBaoConfig;
use crate::openbao::OpenBaoClient;

#[derive(Clone, Copy, Default)]
enum ResponseMode {
    #[default]
    Valid,
    AnotherVersion,
    AnotherKey,
    ShortSignature,
}

struct MockState {
    metadata: Mutex<Value>,
    mode: Mutex<ResponseMode>,
    logins: AtomicUsize,
    signatures: AtomicUsize,
}

struct Fixture {
    client: Arc<OpenBaoClient>,
    state: Arc<MockState>,
    signer: SignerRecord,
    now: AuthorityTime,
    profile: CanonicalRecord<DriverProfile>,
    server: JoinHandle<()>,
    _directory: tempfile::TempDir,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.server.abort();
    }
}

impl Fixture {
    async fn key(&self) -> AuthoritySigningKey {
        AuthoritySigningKey::load(
            self.client.clone(),
            "transit".into(),
            "approved-key".into(),
            NonZeroU32::new(7).unwrap(),
            self.signer.clone(),
        )
        .await
        .unwrap()
    }
}

async fn fixture() -> Fixture {
    let vectors: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../contracts/device-authority.test-vectors.json"
    )))
    .unwrap();
    let case = &vectors["cases"][0];
    let profile: DriverProfile =
        serde_json::from_value(case["records"][1]["record"]["profile"].clone()).unwrap();
    let now = serde_json::from_value(case["now"].clone()).unwrap();
    let mut signer: SignerRecord =
        serde_json::from_value(case["records"][1]["signer"].clone()).unwrap();
    let key = SigningKey::from_bytes(&[42; 32]);
    signer.public_key = HexBytes::new(key.verifying_key().to_bytes());
    let state = Arc::new(MockState {
        metadata: Mutex::new(json!({"data": {
            "name": "approved-key", "type": "ed25519", "derived": false,
            "exportable": false, "allow_plaintext_backup": false, "supports_signing": true,
            "min_encryption_version": 1,
            "keys": {"7": {"public_key": STANDARD.encode(signer.public_key.as_bytes())}}
        }})),
        mode: Mutex::new(ResponseMode::Valid),
        logins: AtomicUsize::new(0),
        signatures: AtomicUsize::new(0),
    });
    let router = Router::new()
        .route(
            "/v1/auth/kubernetes/login",
            post(|State(state): State<Arc<MockState>>, Json(body): Json<Value>| async move {
                assert_eq!(body, json!({"role":"controller", "jwt":"test-workload-jwt"}));
                state
                    .logins
                    .fetch_add(1, Ordering::SeqCst);
                Json(json!({"auth":{"client_token":"test-openbao-token", "lease_duration":60}}))
            }),
        )
        .route(
            "/v1/transit/keys/approved-key",
            get(|State(state): State<Arc<MockState>>, headers: axum::http::HeaderMap| async move {
                assert_eq!(headers["X-Vault-Token"], "test-openbao-token");
                Json(state.metadata.lock().unwrap().clone())
            }),
        )
        .route(
            "/v1/transit/sign/approved-key",
            post(|State(state): State<Arc<MockState>>, Json(body): Json<Value>| async move {
                assert_eq!(
                    body.as_object()
                        .unwrap()
                        .keys()
                        .cloned()
                        .collect::<Vec<_>>(),
                    ["input", "key_version", "prehashed"]
                );
                assert_eq!(body["key_version"], 7);
                assert_eq!(body["prehashed"], false);
                state
                    .signatures
                    .fetch_add(1, Ordering::SeqCst);
                let payload = STANDARD
                    .decode(body["input"].as_str().unwrap())
                    .unwrap();
                let mode = *state.mode.lock().unwrap();
                let seed =
                    if matches!(mode, ResponseMode::AnotherKey) { [43; 32] } else { [42; 32] };
                let signature = SigningKey::from_bytes(&seed)
                    .sign(&payload)
                    .to_bytes();
                let version = if matches!(mode, ResponseMode::AnotherVersion) { 8 } else { 7 };
                let signature = if matches!(mode, ResponseMode::ShortSignature) {
                    STANDARD.encode([0; 32])
                } else {
                    STANDARD.encode(signature)
                };
                Json(json!({"data":{"signature":format!("vault:v{version}:{signature}")}}))
            }),
        )
        .with_state(state.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .unwrap();
    let address = format!("http://{}/", listener.local_addr().unwrap())
        .parse()
        .unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, router)
            .await
            .unwrap();
    });
    let directory = tempfile::tempdir().unwrap();
    let token_file = directory.path().join("workload-token");
    std::fs::write(&token_file, "test-workload-jwt\n").unwrap();
    let client = Arc::new(
        OpenBaoClient::load_test(OpenBaoConfig {
            address: Some(address),
            kubernetes_role: Some("controller".into()),
            service_account_token_file: token_file,
            ..Default::default()
        })
        .await
        .unwrap(),
    );
    Fixture {
        client,
        state,
        signer,
        now,
        profile: CanonicalRecord::new(profile).unwrap(),
        server,
        _directory: directory,
    }
}

#[tokio::test]
async fn signing_uses_the_approved_version_and_shared_workload_token() {
    let fixture = fixture().await;
    let key = fixture.key().await;
    let signature = key
        .sign(&fixture.profile, fixture.now)
        .await
        .unwrap();
    let signed = inari_gateway::device_authority::SignedDriverProfile::from_signature(
        fixture.profile.clone(),
        fixture.signer.key_id.clone(),
        signature,
    );
    signed
        .verify(&fixture.signer, fixture.now)
        .unwrap();
    key.sign(&fixture.profile, fixture.now)
        .await
        .unwrap();
    assert_eq!(
        fixture
            .state
            .logins
            .load(Ordering::SeqCst),
        1
    );
    assert_eq!(
        fixture
            .state
            .signatures
            .load(Ordering::SeqCst),
        2
    );
    assert!(!format!("{key:?}").contains("test-openbao-token"));
}

#[tokio::test]
async fn changed_key_safety_or_public_key_blocks_signing() {
    let fixture = fixture().await;
    let key = fixture.key().await;
    let original = fixture
        .state
        .metadata
        .lock()
        .unwrap()
        .clone();
    for field in ["derived", "exportable", "allow_plaintext_backup"] {
        let mut metadata = original.clone();
        metadata["data"][field] = json!(true);
        *fixture.state.metadata.lock().unwrap() = metadata;
        assert!(
            key.sign(&fixture.profile, fixture.now)
                .await
                .is_err(),
            "{field}"
        );
    }
    for (field, value) in [
        ("type", json!("ecdsa-p256")),
        ("name", json!("another-key")),
        ("supports_signing", json!(false)),
        ("min_encryption_version", json!(8)),
        ("keys", json!({"7":{"public_key":STANDARD.encode([43;32])}})),
        ("keys", json!({"8":{"public_key":STANDARD.encode(fixture.signer.public_key.as_bytes())}})),
    ] {
        let mut metadata = original.clone();
        metadata["data"][field] = value;
        *fixture.state.metadata.lock().unwrap() = metadata;
        assert!(
            key.sign(&fixture.profile, fixture.now)
                .await
                .is_err(),
            "{field}"
        );
    }
    assert_eq!(
        fixture
            .state
            .signatures
            .load(Ordering::SeqCst),
        0
    );
}

#[tokio::test]
async fn invalid_returned_signature_or_version_never_leaves_the_signer() {
    let fixture = fixture().await;
    let key = fixture.key().await;
    for mode in
        [ResponseMode::AnotherVersion, ResponseMode::AnotherKey, ResponseMode::ShortSignature]
    {
        *fixture.state.mode.lock().unwrap() = mode;
        assert!(
            key.sign(&fixture.profile, fixture.now)
                .await
                .is_err()
        );
    }
}

#[tokio::test]
async fn wrong_purpose_retired_or_expired_approval_blocks_before_the_request() {
    let fixture = fixture().await;
    for purpose in [
        SignerPurpose::AuthorityRevision,
        SignerPurpose::CertificationMatrix,
        SignerPurpose::BindingRevision,
    ] {
        let mut signer = fixture.signer.clone();
        signer.purpose = purpose;
        let key = AuthoritySigningKey::load(
            fixture.client.clone(),
            "transit".into(),
            "approved-key".into(),
            NonZeroU32::new(7).unwrap(),
            signer,
        )
        .await
        .unwrap();
        assert!(
            key.sign(&fixture.profile, fixture.now)
                .await
                .is_err()
        );
    }
    let mut signer = fixture.signer.clone();
    signer.state = SignerState::Retired;
    signer.retired_at = Some(fixture.now);
    let key = AuthoritySigningKey::load(
        fixture.client.clone(),
        "transit".into(),
        "approved-key".into(),
        NonZeroU32::new(7).unwrap(),
        signer,
    )
    .await
    .unwrap();
    assert!(
        key.sign(&fixture.profile, fixture.now)
            .await
            .is_err()
    );
    let key = fixture.key().await;
    assert!(
        key.sign(&fixture.profile, fixture.signer.not_after.unwrap())
            .await
            .is_err()
    );
    assert!(
        key.sign(
            &fixture.profile,
            AuthorityTime::new(fixture.signer.not_before.get() - chrono::Duration::seconds(1))
                .unwrap()
        )
        .await
        .is_err()
    );
    assert_eq!(
        fixture
            .state
            .signatures
            .load(Ordering::SeqCst),
        0
    );
}

#[tokio::test]
async fn agent_purposes_and_path_components_cannot_load_a_controller_key() {
    let fixture = fixture().await;
    for purpose in [SignerPurpose::DeviceObservation, SignerPurpose::DeviceTestEvidence] {
        let mut signer = fixture.signer.clone();
        signer.purpose = purpose;
        assert!(
            AuthoritySigningKey::load(
                fixture.client.clone(),
                "transit".into(),
                "approved-key".into(),
                NonZeroU32::new(7).unwrap(),
                signer
            )
            .await
            .is_err()
        );
    }
    for name in ["../key", "key/name", "key%2fname", ""] {
        assert!(
            AuthoritySigningKey::load(
                fixture.client.clone(),
                "transit".into(),
                name.into(),
                NonZeroU32::new(7).unwrap(),
                fixture.signer.clone()
            )
            .await
            .is_err()
        );
    }
    assert_eq!(
        fixture
            .state
            .logins
            .load(Ordering::SeqCst),
        0
    );
}
