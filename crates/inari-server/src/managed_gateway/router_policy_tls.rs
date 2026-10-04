use std::net::TcpListener;
use std::sync::atomic::{AtomicU8, AtomicUsize, Ordering};

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::put;
use axum::{Json, Router};
use inari_router::management::ManagementAcceptor;
use rcgen::{
    BasicConstraints, CertificateParams, CertifiedIssuer, DnType, ExtendedKeyUsagePurpose, IsCa,
    KeyPair, KeyUsagePurpose,
};

use super::*;

fn issuer(name: &str) -> CertifiedIssuer<'static, KeyPair> {
    let mut params = CertificateParams::default();
    params
        .distinguished_name
        .push(DnType::CommonName, name);
    params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    CertifiedIssuer::self_signed(params, KeyPair::generate().unwrap()).unwrap()
}

fn certificate(
    issuer: &CertifiedIssuer<'_, KeyPair>,
    name: &str,
    usage: ExtendedKeyUsagePurpose,
) -> (String, String) {
    let key = KeyPair::generate().unwrap();
    let mut params = CertificateParams::new(vec!["localhost".into()]).unwrap();
    params
        .distinguished_name
        .push(DnType::CommonName, name);
    params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
    params.extended_key_usages = vec![usage];
    (
        params
            .signed_by(&key, issuer)
            .unwrap()
            .pem(),
        key.serialize_pem(),
    )
}

#[derive(Default)]
struct Endpoint {
    mode: AtomicU8,
    requests: AtomicUsize,
    redirects: AtomicUsize,
}

async fn apply(State(state): State<Arc<Endpoint>>, Json(policy): Json<SignedPolicy>) -> Response {
    state
        .requests
        .fetch_add(1, Ordering::SeqCst);
    let verified = policy
        .verify(&SigningKey::from_bytes(&[71; 32]).verifying_key(), "test_fleet")
        .unwrap();
    match state.mode.load(Ordering::SeqCst) {
        1 => Redirect::temporary("/redirect-target").into_response(),
        2 => " "
            .repeat(MAX_RESPONSE_BYTES + 1)
            .into_response(),
        _ => Json(RouterStatus {
            generation: Some(policy.policy.generation),
            digest: Some(verified.digest().into()),
            expires_at: Some(policy.policy.expires_at),
            ready: true,
            message: "Test Router acknowledgment.".into(),
        })
        .into_response(),
    }
}

#[tokio::test]
async fn controller_management_uses_dedicated_mutual_tls_and_bounded_responses() {
    let directory = tempfile::tempdir().unwrap();
    let root = issuer("management-root");
    let data_root = issuer("data-plane-root");
    let (server_cert, server_key) =
        certificate(&root, "router-management", ExtendedKeyUsagePurpose::ServerAuth);
    let (client_cert, client_key) =
        certificate(&root, "controller-policy", ExtendedKeyUsagePurpose::ClientAuth);
    let path = |name: &str| directory.path().join(name);
    for (name, contents) in [
        ("root.pem", root.pem()),
        ("server.pem", server_cert),
        ("server.key", server_key),
        ("client.pem", client_cert),
        ("client.key", client_key),
        ("data-root.pem", data_root.pem()),
    ] {
        std::fs::write(path(name), contents).unwrap();
    }
    let acceptor = ManagementAcceptor::from_files(
        &path("server.pem"),
        &path("server.key"),
        &path("root.pem"),
        "controller-policy",
    )
    .await
    .unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let port = listener.local_addr().unwrap().port();
    let endpoint = Arc::new(Endpoint::default());
    let routes = Router::new()
        .route("/policy", put(apply))
        .route(
            "/redirect-target",
            put(|State(state): State<Arc<Endpoint>>| async move {
                state
                    .redirects
                    .fetch_add(1, Ordering::SeqCst);
                StatusCode::OK
            }),
        )
        .with_state(endpoint.clone());
    let shutdown = axum_server::Handle::new();
    let server = axum_server::from_tcp(listener)
        .unwrap()
        .acceptor(acceptor)
        .handle(shutdown.clone());
    let task = tokio::spawn(server.serve(routes.into_make_service()));
    let mut config = super::super::router_policy_tests::config();
    config.management_ca_file = path("root.pem");
    config.management_certificate_file = path("client.pem");
    config.management_private_key_file = path("client.key");
    let router = RouterManagementConfig {
        router_id: "test_router".into(),
        address: format!("https://localhost:{port}/")
            .parse()
            .unwrap(),
    };
    let now = Utc::now().trunc_subsecs(6);
    let signed = SignedPolicy::sign(
        Policy {
            version: 1,
            fleet_id: "test_fleet".into(),
            generation: 1,
            issued_at: now,
            not_before: now,
            expires_at: now + POLICY_LIFETIME,
            namespace_prefix: "iot/v1/agents".into(),
            trusted_peer_common_names: vec!["controller_test".into()],
            agents: vec![],
        },
        &SigningKey::from_bytes(&[71; 32]),
    )
    .unwrap();
    let client = HttpsManagement::load(&config)
        .await
        .unwrap();
    let status = client
        .apply(&router, &signed)
        .await
        .unwrap();
    assert!(status.is_current());
    assert_eq!(status.generation, Some(1));
    assert_eq!(endpoint.requests.load(Ordering::SeqCst), 1);
    let wrong_host = RouterManagementConfig {
        address: format!("https://127.0.0.1:{port}/")
            .parse()
            .unwrap(),
        ..router.clone()
    };
    assert!(
        client
            .apply(&wrong_host, &signed)
            .await
            .is_err(),
        "the Router hostname must match its certificate"
    );
    config.management_ca_file = path("data-root.pem");
    assert!(
        HttpsManagement::load(&config)
            .await
            .unwrap()
            .apply(&router, &signed)
            .await
            .is_err(),
        "the data-plane root cannot replace the management root"
    );
    config.management_ca_file = path("root.pem");
    let (agent_cert, agent_key) =
        certificate(&root, "agt_000000000000000000000001", ExtendedKeyUsagePurpose::ClientAuth);
    std::fs::write(path("client.pem"), agent_cert).unwrap();
    std::fs::write(path("client.key"), agent_key).unwrap();
    assert!(
        HttpsManagement::load(&config)
            .await
            .unwrap()
            .apply(&router, &signed)
            .await
            .is_err(),
        "an Agent cannot manage a Router"
    );
    assert_eq!(endpoint.requests.load(Ordering::SeqCst), 1, "TLS failures never reach HTTP");
    endpoint.mode.store(1, Ordering::SeqCst);
    assert!(
        client
            .apply(&router, &signed)
            .await
            .is_err()
    );
    assert_eq!(
        endpoint
            .redirects
            .load(Ordering::SeqCst),
        0,
        "management never follows a redirect"
    );
    endpoint.mode.store(2, Ordering::SeqCst);
    assert!(
        client
            .apply(&router, &signed)
            .await
            .is_err(),
        "an oversized response must be rejected"
    );
    shutdown.shutdown();
    task.await.unwrap().unwrap();
}

#[tokio::test]
async fn readiness_requires_an_unexpired_complete_acknowledgment() {
    use crate::state::ReadinessLevel;
    use sea_orm::DatabaseConnection;

    let controller = super::super::router_policy_tests::admission_controller(
        GatewayRepository::new(DatabaseConnection::from(
            sqlx::postgres::PgPoolOptions::new()
                .connect_lazy("postgres://unused:unused@127.0.0.1/unused")
                .unwrap(),
        )),
        "org_test",
        "iot/v1/agents",
    );
    assert_eq!(controller.readiness().status().level(), ReadinessLevel::Starting);
    controller
        .health
        .send_replace(RouterPolicyHealth::ReadyUntil(Utc::now() + TimeDelta::seconds(10)));
    assert_eq!(controller.readiness().status().level(), ReadinessLevel::Ready);
    controller
        .health
        .send_replace(RouterPolicyHealth::ReadyUntil(Utc::now() - TimeDelta::seconds(1)));
    assert_eq!(controller.readiness().status().level(), ReadinessLevel::Degraded);
    controller
        .health
        .send_replace(RouterPolicyHealth::Unavailable);
    assert_eq!(controller.readiness().status().level(), ReadinessLevel::Degraded);
}
