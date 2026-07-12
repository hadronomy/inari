use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use inari_gateway::audit::AuditContext;
use inari_gateway::identity::ActorId;
use inari_gateway::onboarding::{
    CertificateMode, CreateInvitation, OnboardingConfig, OnboardingService,
};
use inari_gateway::protocol::ProtocolVersion;
use inari_migration::{Migrator, MigratorTrait};
use inari_server::config::{LoadedConfig, ZenohConfig};
use inari_server::http;
use inari_server::state::AppState;
use inari_server::zenoh::ZenohSupervisor;
use leptos::prelude::LeptosOptions;
use sea_orm::DatabaseConnection;
use tower::ServiceExt;

async fn test_app() -> (axum::Router, OnboardingService) {
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
    loaded.settings.managed_gateway.enabled = true;
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
    let state = AppState::new_with_onboarding(
        loaded,
        zenoh,
        LeptosOptions::builder()
            .output_name("inari-web")
            .site_root("target/site")
            .build(),
        Some(onboarding.clone()),
        None,
        None,
    );
    let app = http::router(&state)
        .expect("router should build")
        .with_state(state);
    (app, onboarding)
}

async fn assert_public_preview_and_setup_never_expose_invitation_secret() {
    let (app, onboarding) = test_app().await;
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
