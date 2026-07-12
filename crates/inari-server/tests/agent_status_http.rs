use std::sync::Arc;

use axum::body::{Body, to_bytes};
use axum::http::header::{AUTHORIZATION, CONTENT_TYPE, WWW_AUTHENTICATE};
use axum::http::{Request, StatusCode};
use chrono::{TimeZone, Utc};
use inari_gateway::credentials::TokenDigest;
use inari_gateway::onboarding::{OnboardingConfig, OnboardingService};
use inari_gateway::protocol::{AgentPublication, GatewaySnapshot, ProtocolVersion};
use inari_gateway::{AgentEnrollmentRecord, EnrollmentCredential};
use inari_server::config::{LoadedConfig, ZenohConfig};
use inari_server::http;
use inari_server::protocol::InariProtocolModule;
use inari_server::state::AppState;
use inari_server::zenoh::ZenohSupervisor;
use leptos::prelude::LeptosOptions;
use serde_json::{Value, json};
use tempfile::TempDir;
use tower::ServiceExt;

const AGENT_ID: &str = "agt_browser_audit";
const READ_TOKEN: &str = "read-api-token";
const READ_TOKEN_DIGEST: &str = "52f5356b451cde75f687831123aa0d4be18e9fd77cab01f541539d3956c45dae";

struct TestApp {
    router: axum::Router,
    _temp: TempDir,
}

impl TestApp {
    async fn spawn() -> Self {
        let temp = tempfile::tempdir().expect("temporary directory should be created");
        let database_path = temp.path().join("gateway.sqlite3");
        let onboarding = OnboardingService::initialize(OnboardingConfig {
            database_path: database_path.clone(),
            enabled: false,
            public_base_url: None,
            controller_name: Some("Test Controller".into()),
            controller_instance_id: "controller-test".into(),
            operator_token_hashes: Vec::new(),
            invitation_ttl: std::time::Duration::from_secs(600),
            supported_protocol_versions: vec![ProtocolVersion::current()],
            certificate_mode: inari_gateway::onboarding::CertificateMode::None,
            requires_mutual_tls_after_issuance: false,
        })
        .await
        .expect("onboarding repository should initialize");
        let repository = onboarding.repository().clone();
        let enrolled_at = Utc
            .with_ymd_and_hms(2026, 7, 12, 8, 0, 0)
            .single()
            .expect("test time should be valid");
        repository
            .enroll_agent(
                AgentEnrollmentRecord {
                    agent_id: AGENT_ID.into(),
                    key_id: "kid_browser_audit".into(),
                    jwk_thumbprint: "thumbprint".into(),
                    public_jwk: json!({}),
                    certificate_pem: None,
                    namespace: format!("iot/v1/agents/{AGENT_ID}"),
                    protocol_version: ProtocolVersion::current(),
                    controller_actions: Vec::new(),
                    enrolled_at,
                },
                EnrollmentCredential::ConfiguredToken { digest: [7_u8; 32] },
                &json!({}),
            )
            .await
            .expect("agent should be enrolled");
        repository
            .enroll_agent(
                AgentEnrollmentRecord {
                    agent_id: "agt_silent".into(),
                    key_id: "kid_silent".into(),
                    jwk_thumbprint: "silent-thumbprint".into(),
                    public_jwk: json!({}),
                    certificate_pem: None,
                    namespace: "iot/v1/agents/agt_silent".into(),
                    protocol_version: ProtocolVersion::current(),
                    controller_actions: Vec::new(),
                    enrolled_at,
                },
                EnrollmentCredential::ConfiguredToken { digest: [8_u8; 32] },
                &json!({}),
            )
            .await
            .expect("silent agent should be enrolled");

        for (offset, message_id) in [(1, "msg_old"), (2, "msg_latest")] {
            repository
                .record_publication(
                    AGENT_ID,
                    &format!("iot/v1/agents/{AGENT_ID}/status/latest"),
                    &status_publication(message_id, offset),
                    enrolled_at + chrono::Duration::seconds(offset),
                )
                .await
                .expect("status should be recorded");
        }

        let mut loaded = LoadedConfig::default();
        loaded.settings.managed_gateway.enabled = true;
        loaded
            .settings
            .managed_gateway
            .database_path = database_path;
        loaded
            .settings
            .managed_gateway
            .api
            .read_token_hashes = vec![
            READ_TOKEN_DIGEST
                .parse::<TokenDigest>()
                .expect("read API token digest should parse"),
        ];
        let (zenoh, _) = ZenohSupervisor::new(ZenohConfig::default());
        let state = AppState::new_with_onboarding(
            loaded,
            zenoh,
            Arc::new(InariProtocolModule),
            LeptosOptions::builder()
                .output_name("inari-web")
                .site_root("target/site")
                .build(),
            Some(onboarding),
        );
        let router = http::router(&state)
            .expect("router should build")
            .with_state(state);
        Self { router, _temp: temp }
    }

    async fn get(&self, path: &str, token: Option<&str>) -> axum::response::Response {
        let mut request = Request::builder().uri(path);
        if let Some(token) = token {
            request = request.header(AUTHORIZATION, format!("Bearer {token}"));
        }
        self.router
            .clone()
            .oneshot(
                request
                    .body(Body::empty())
                    .expect("request should build"),
            )
            .await
            .expect("router should respond")
    }
}

fn status_publication(message_id: &str, offset: i64) -> AgentPublication {
    let generated_at = Utc
        .with_ymd_and_hms(2026, 7, 12, 8, 0, offset as u32)
        .single()
        .expect("test time should be valid");
    let snapshot = serde_json::from_value::<GatewaySnapshot>(json!({
        "generated_at": generated_at,
        "protocol": {
            "version": "2026-07-11",
            "supported_versions": ["2026-07-11"]
        },
        "service": { "name": "inari-agent", "version": "test" },
        "security": {
            "mode": "managed",
            "exposure": "loopback",
            "tls_required": true,
            "certificate_mode": "managed",
            "mutual_tls_mode": "required",
            "mutual_tls_enabled": true
        },
        "runtime": {},
        "capabilities": { "transport": "zenoh" }
    }))
    .expect("gateway snapshot should deserialize");
    AgentPublication::StatusSnapshot { message_id: message_id.into(), snapshot: Box::new(snapshot) }
}

async fn json_body(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body should be readable");
    serde_json::from_slice(&body).expect("response body should be JSON")
}

#[tokio::test]
async fn status_returns_the_latest_durable_observation() {
    let app = TestApp::spawn().await;
    let response = app
        .get("/api/inari/v1/agents/agt_browser_audit/status", Some(READ_TOKEN))
        .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()[CONTENT_TYPE], "application/json");
    let status = json_body(response).await;
    assert_eq!(status["agent_id"], AGENT_ID);
    assert_eq!(status["message_id"], "msg_latest");
    assert_eq!(status["snapshot"]["service"]["name"], "inari-agent");
}

#[tokio::test]
async fn status_requires_its_own_bearer_credential() {
    let app = TestApp::spawn().await;

    for token in [None, Some("wrong-token")] {
        let response = app
            .get("/api/inari/v1/agents/agt_browser_audit/status", token)
            .await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(response.headers()[CONTENT_TYPE], "application/problem+json");
        assert_eq!(response.headers()[WWW_AUTHENTICATE], "Bearer realm=\"inari-api\"");
        assert_eq!(json_body(response).await["code"], "unauthorized");
    }
}

#[tokio::test]
async fn malformed_agent_ids_return_problem_details() {
    let app = TestApp::spawn().await;
    let response = app
        .get("/api/inari/v1/agents/not-an-agent/status", Some(READ_TOKEN))
        .await;

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(response.headers()[CONTENT_TYPE], "application/problem+json");
    assert_eq!(json_body(response).await["code"], "bad_request");
}

#[tokio::test]
async fn enrolled_agents_without_status_return_problem_details() {
    let app = TestApp::spawn().await;
    let response = app
        .get("/api/inari/v1/agents/agt_silent/status", Some(READ_TOKEN))
        .await;

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(response.headers()[CONTENT_TYPE], "application/problem+json");
    let problem = json_body(response).await;
    assert_eq!(problem["code"], "not_found");
    assert_eq!(problem["detail"], "No status has been observed for this agent.");
}
