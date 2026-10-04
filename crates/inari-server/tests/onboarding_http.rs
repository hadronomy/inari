use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::Utc;
use ed25519_dalek::SigningKey;
use futures_util::future::BoxFuture;
use inari_gateway::audit::AuditContext;
use inari_gateway::certificate::{CertificateIssuer, CertificateRequest};
use inari_gateway::identity::ActorId;
use inari_gateway::onboarding::{
    CertificateMode, CreateInvitation, InvitationCode, InvitationState, OnboardingConfig,
    OnboardingService,
};
use inari_gateway::protocol::{
    CertificateBootstrapAuth, CertificateBootstrapAuthKind, CertificateProvisioning,
    EnrollmentResponse, ProtocolDescriptor, ProtocolVersion,
};
use inari_gateway::{GatewayError, GatewayResult};
use inari_migration::{Migrator, MigratorTrait};
use inari_router::SignedPolicy;
use inari_router::supervisor::RouterStatus;
use inari_server::config::{
    LoadedConfig, ManagedGatewayCertificateMode, RouterManagementConfig, RouterPolicyConfig,
    ZenohConfig,
};
use inari_server::error::AppResult;
use inari_server::http;
use inari_server::managed_gateway::{
    ManagedGatewaySecurity, RouterAdmissionController, RouterManagement,
};
use inari_server::state::AppState;
use inari_server::zenoh::ZenohSupervisor;
use leptos::prelude::LeptosOptions;
use sea_orm::DatabaseConnection;
use serde_json::json;
use sha2::{Digest, Sha256};
use tower::ServiceExt;

/// The fixture CSR is signed by the Ed25519 key with seed 1.
const CSR: &str = include_str!("../../inari-gateway/tests/fixtures/enrollment/valid.csr.pem");

struct TestRouter;

impl RouterManagement for TestRouter {
    fn apply<'a>(
        &'a self,
        _router: &'a RouterManagementConfig,
        policy: &'a SignedPolicy,
    ) -> BoxFuture<'a, AppResult<RouterStatus>> {
        Box::pin(async move {
            let verified = policy
                .verify(&SigningKey::from_bytes(&[71; 32]).verifying_key(), "test_fleet")
                .unwrap();
            Ok(RouterStatus {
                generation: Some(policy.policy.generation),
                digest: Some(verified.digest().into()),
                expires_at: Some(policy.policy.expires_at),
                ready: true,
                message: "Test Router.".into(),
            })
        })
    }
}

/// Stands in for step-ca token signing and records each call.
#[derive(Default)]
struct RecordingIssuer {
    calls: AtomicUsize,
    unavailable: AtomicBool,
}

impl CertificateIssuer for RecordingIssuer {
    fn issue(&self, _request: &CertificateRequest) -> GatewayResult<CertificateBootstrapAuth> {
        let call = self
            .calls
            .fetch_add(1, Ordering::SeqCst)
            + 1;
        if self.unavailable.load(Ordering::SeqCst) {
            return Err(GatewayError::Unavailable("step-ca token signing failed".into()));
        }
        Ok(CertificateBootstrapAuth {
            kind: CertificateBootstrapAuthKind::Ott,
            token: Some(format!("ott-{call}")),
            expires_at: None,
        })
    }
}

async fn test_app(issuer: Arc<RecordingIssuer>) -> (axum::Router, OnboardingService) {
    let database_url = std::env::var("INARI_TEST_DATABASE_URL")
        .expect("INARI_TEST_DATABASE_URL is required for PostgreSQL integration tests");
    let pool = sqlx::PgPool::connect(&database_url)
        .await
        .expect("test database should connect");
    let database = DatabaseConnection::from(pool);
    Migrator::up(&database, None)
        .await
        .expect("test database should migrate");
    let repository = inari_gateway::GatewayRepository::new(database);
    let onboarding = OnboardingService::initialize(
        OnboardingConfig {
            organization_id: "org_test"
                .parse()
                .expect("organization ID should parse"),
            organization_name: "Test organization".into(),
            default_site_id: "site_test"
                .parse()
                .expect("site ID should parse"),
            default_site_name: "Test site".into(),
            enabled: true,
            public_base_url: Some(
                "https://controller.example.com/"
                    .parse()
                    .expect("test URL should parse"),
            ),
            controller_name: Some("Test Controller".into()),
            controller_instance_id: "controller-test".into(),
            invitation_ttl: std::time::Duration::from_secs(600),
            supported_protocol_versions: vec![ProtocolVersion::current()],
            certificate_mode: CertificateMode::StepCa,
            requires_mutual_tls_after_issuance: true,
        },
        repository,
    )
    .await
    .expect("onboarding should initialize");
    let mut loaded = LoadedConfig::default();
    loaded.settings.organization.id = "org_test"
        .parse()
        .expect("organization ID should parse");
    loaded
        .settings
        .organization
        .default_site_id = "site_test"
        .parse()
        .expect("site ID should parse");
    loaded.settings.managed_gateway.enabled = true;
    loaded
        .settings
        .managed_gateway
        .data_plane
        .connect_endpoints = vec!["tls/router.example.com:7447".into()];
    let certificate = &mut loaded
        .settings
        .managed_gateway
        .certificate;
    certificate.mode = ManagedGatewayCertificateMode::StepCa;
    certificate.step_ca_base_url = Some(
        "https://ca.example.com/"
            .parse()
            .expect("CA URL should parse"),
    );
    certificate.step_ca_root_fingerprint = Some("a".repeat(64));
    loaded
        .settings
        .managed_gateway
        .onboarding
        .enabled = true;
    loaded
        .settings
        .managed_gateway
        .onboarding
        .public_base_url = Some("https://controller.example.com/".into());
    let (zenoh, _) = ZenohSupervisor::new(ZenohConfig::default());
    let admission = Arc::new(
        RouterAdmissionController::new(
            onboarding.repository().clone(),
            "org_test".into(),
            RouterPolicyConfig {
                fleet_id: "test_fleet".into(),
                routers: vec![RouterManagementConfig {
                    router_id: "test_router".into(),
                    address: "https://router.example/"
                        .parse()
                        .unwrap(),
                }],
                trusted_peer_common_names: vec!["controller_test".into()],
                signing_key_file: "/test/policy-key".into(),
                management_ca_file: "/test/management-ca".into(),
                management_certificate_file: "/test/management-cert".into(),
                management_private_key_file: "/test/management-key".into(),
            },
            loaded
                .settings
                .managed_gateway
                .data_plane
                .namespace_prefix
                .clone(),
            SigningKey::from_bytes(&[71; 32]),
            Arc::new(TestRouter),
        )
        .unwrap(),
    );
    let state = AppState::new_with_onboarding(
        loaded,
        zenoh,
        LeptosOptions::builder()
            .output_name("inari-web")
            .site_root("target/site")
            .build(),
        Some(onboarding.clone()),
        None,
        ManagedGatewaySecurity {
            certificate_issuer: Some(issuer),
            router_admission: Some(admission),
            ..Default::default()
        },
    );
    let app = http::router(&state)
        .expect("router should build")
        .with_state(state);
    (app, onboarding)
}

async fn assert_public_preview_and_setup_never_expose_invitation_secret() {
    let (app, onboarding) = test_app(Arc::default()).await;
    let invitation = onboarding
        .create_invitation(
            CreateInvitation { label: Some("Front desk".into()) },
            &AuditContext::new(ActorId::from_oidc_subject("test-operator"), None),
        )
        .await
        .expect("invitation should be created");

    let preview = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/inari/v1/invitations/{}", invitation.invitation_id))
                .body(Body::empty())
                .expect("request should build"),
        )
        .await
        .expect("router should respond");
    assert_eq!(preview.status(), StatusCode::OK);
    let preview_body = to_bytes(preview.into_body(), usize::MAX)
        .await
        .expect("preview body should be readable");
    assert!(!String::from_utf8_lossy(&preview_body).contains(&invitation.manual_code));

    let setup = app
        .oneshot(
            Request::builder()
                .uri(format!("/setup/{}", invitation.invitation_id))
                .body(Body::empty())
                .expect("request should build"),
        )
        .await
        .expect("router should respond");
    assert_eq!(setup.status(), StatusCode::OK);
    let csp = setup
        .headers()
        .get("content-security-policy")
        .and_then(|value| value.to_str().ok())
        .expect("setup response should include CSP");
    assert!(csp.contains("'wasm-unsafe-eval'"));
    assert!(csp.contains("'nonce-"));
    assert_eq!(
        setup
            .headers()
            .get("cache-control")
            .unwrap(),
        "no-store"
    );
    let setup_body = to_bytes(setup.into_body(), usize::MAX)
        .await
        .expect("setup body should be readable");
    assert!(!String::from_utf8_lossy(&setup_body).contains(&invitation.manual_code));
}

#[tokio::test(flavor = "current_thread")]
#[ignore = "requires INARI_TEST_DATABASE_URL"]
async fn public_preview_and_setup_never_expose_invitation_secret() {
    tokio::task::LocalSet::new()
        .run_until(assert_public_preview_and_setup_never_expose_invitation_secret())
        .await;
}

fn enrollment_request() -> serde_json::Value {
    let public = SigningKey::from_bytes(&[1; 32])
        .verifying_key()
        .to_bytes();
    let digest = format!("{:x}", Sha256::digest(public));
    let state = SigningKey::from_bytes(&[2; 32])
        .verifying_key()
        .to_bytes();
    json!({
        "protocol": ProtocolDescriptor::default(),
        "agent_id": format!("agt_{}", &digest[..24]),
        "key_id": format!("kid_{}", &digest[..12]),
        "public_jwk": {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
            "kid": format!("kid_{}", &digest[..12]), "x": URL_SAFE_NO_PAD.encode(public)},
        "dispatch_key": {"key_id": "dispatch_http", "kem": "dhkem_x25519_hkdf_sha256",
            "public_key_base64url": URL_SAFE_NO_PAD.encode([42; 32])},
        "state_signing_jwk": {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
            "kid": format!("agent_state_{:x}", Sha256::digest(state)), "x": URL_SAFE_NO_PAD.encode(state)},
        "csr_pem": CSR,
        "snapshot": {"generated_at": Utc::now(), "protocol": ProtocolDescriptor::default(),
            "service": {}, "runtime": {},
            "capabilities": {"transport": "https+zenoh"},
            "security": {"mode": "managed", "exposure": "private", "tls_required": true,
                "certificate_mode": "step_ca", "mutual_tls_mode": "required", "mutual_tls_enabled": true}},
    })
}

async fn post_enrollment(app: &axum::Router, credential: &str) -> (StatusCode, Vec<u8>) {
    let response = app
        .clone()
        .oneshot(
            Request::post("/api/inari/v1/enrollments")
                .header("authorization", format!("Bearer {credential}"))
                .header("content-type", "application/json")
                .body(Body::from(enrollment_request().to_string()))
                .expect("request should build"),
        )
        .await
        .expect("router should respond");
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("enrollment body should be readable");
    (status, body.to_vec())
}

fn one_time_token(body: &[u8]) -> (Option<String>, chrono::DateTime<Utc>) {
    let response: EnrollmentResponse =
        serde_json::from_slice(body).expect("enrollment response should parse");
    let token = response
        .certificate
        .and_then(|CertificateProvisioning::StepCa { enrollment }| enrollment.bootstrap_auth)
        .and_then(|auth| auth.token);
    (token, response.enrolled_at)
}

async fn assert_enrollment_consumes_the_invitation_once() {
    let issuer = Arc::new(RecordingIssuer::default());
    let (app, onboarding) = test_app(issuer.clone()).await;
    let invitation = onboarding
        .create_invitation(
            CreateInvitation { label: Some("Front desk".into()) },
            &AuditContext::new(ActorId::from_oidc_subject("test-operator"), None),
        )
        .await
        .expect("invitation should be created");
    let code: InvitationCode = invitation
        .manual_code
        .parse()
        .expect("manual code should parse");
    let state = || async {
        onboarding
            .repository()
            .invitation(invitation.invitation_id.as_str(), Utc::now())
            .await
            .expect("invitation should load")
            .state
    };

    let other = InvitationCode::generate()
        .expect("code should generate")
        .normalized();
    let normalized = code.normalized();
    let split = normalized
        .find(code.id().as_str())
        .expect("the code should contain its ID")
        + code.id().as_str().len();
    let wrong = format!("{}{}", &normalized[..split], &other[split..]);
    assert_eq!(post_enrollment(&app, &wrong).await.0, StatusCode::FORBIDDEN);
    assert_eq!(issuer.calls.load(Ordering::SeqCst), 0);

    issuer
        .unavailable
        .store(true, Ordering::SeqCst);
    let (status, _) = post_enrollment(&app, &invitation.manual_code).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(state().await, InvitationState::Created);

    issuer
        .unavailable
        .store(false, Ordering::SeqCst);
    let (status, body) = post_enrollment(&app, &invitation.manual_code).await;
    assert_eq!(status, StatusCode::OK);
    let (first_token, first_enrolled_at) = one_time_token(&body);
    assert_eq!(state().await, InvitationState::Enrolled);

    // The response was lost. The same Agent retries with the same request.
    let (status, body) = post_enrollment(&app, &invitation.manual_code).await;
    assert_eq!(status, StatusCode::OK);
    let (retry_token, retry_enrolled_at) = one_time_token(&body);
    assert_eq!(first_token.as_deref(), Some("ott-2"));
    assert_eq!(retry_token.as_deref(), Some("ott-3"));
    assert_eq!(retry_enrolled_at, first_enrolled_at);
}

#[tokio::test(flavor = "current_thread")]
#[ignore = "requires INARI_TEST_DATABASE_URL"]
async fn enrollment_consumes_the_invitation_once() {
    tokio::task::LocalSet::new()
        .run_until(assert_enrollment_consumes_the_invitation_once())
        .await;
}
