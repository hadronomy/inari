use std::convert::Infallible;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::post;
use axum::{Json, Router};
use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use bytes::Bytes;
use serde_json::{Value, json};
use tokio::task::JoinHandle;

use super::{ManagedPayloadKeyWrapper, OpenBaoTransitKeyWrapper};
use crate::config::ManagedGatewayPayloadProtectionConfig;

struct Fixture {
    wrapper: OpenBaoTransitKeyWrapper,
    server: JoinHandle<()>,
    _directory: tempfile::TempDir,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.server.abort();
    }
}

async fn fixture(router: Router) -> Fixture {
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
    let service_account_token_file = directory.path().join("workload-token");
    std::fs::write(&service_account_token_file, "test-workload-jwt\n").unwrap();
    let wrapper = OpenBaoTransitKeyWrapper::load(ManagedGatewayPayloadProtectionConfig {
        address: Some(address),
        service_account_token_file,
        kubernetes_role: Some("controller".into()),
        transit_key_name: "payloads".into(),
        namespace: Some("test".into()),
        ..Default::default()
    })
    .await
    .unwrap();
    Fixture { wrapper, server, _directory: directory }
}

#[tokio::test]
async fn transit_wrap_and_unwrap_use_workload_authentication_and_aad() {
    let logins = Arc::new(AtomicUsize::new(0));
    let router = Router::new()
        .route("/v1/auth/kubernetes/login", post(|State(logins): State<Arc<AtomicUsize>>, headers: HeaderMap, Json(body): Json<Value>| async move {
            assert_eq!(body, json!({"role": "controller", "jwt": "test-workload-jwt"}));
            assert_eq!(headers["X-Vault-Namespace"], "test");
            assert!(!headers.contains_key("X-Vault-Token"));
            logins.fetch_add(1, Ordering::SeqCst);
            Json(json!({"auth": {"client_token": "test-transit-token", "lease_duration": 60}}))
        }))
        .route("/v1/transit/encrypt/payloads", post(|headers: HeaderMap, Json(body): Json<Value>| async move {
            assert_eq!(headers["X-Vault-Token"], "test-transit-token");
            assert_eq!(headers["X-Vault-Namespace"], "test");
            assert_eq!(body, json!({"plaintext": STANDARD.encode([9u8; 32]), "associated_data": STANDARD.encode(b"work context")}));
            Json(json!({"data": {"ciphertext": "vault:v3:d3JhcHBlZA=="}}))
        }))
        .route("/v1/transit/decrypt/payloads", post(|headers: HeaderMap, Json(body): Json<Value>| async move {
            assert_eq!(headers["X-Vault-Token"], "test-transit-token");
            assert_eq!(body, json!({"ciphertext": "vault:v3:d3JhcHBlZA==", "associated_data": STANDARD.encode(b"work context")}));
            Json(json!({"data": {"plaintext": STANDARD.encode([9u8; 32])}}))
        }))
        .with_state(logins.clone());
    let fixture = fixture(router).await;
    let wrapped = fixture
        .wrapper
        .wrap(&[9; 32], b"work context")
        .await
        .unwrap();
    assert_eq!(wrapped.key_version, 3);
    let unwrapped = fixture
        .wrapper
        .unwrap(&wrapped.ciphertext, b"work context")
        .await
        .unwrap();
    assert_eq!(&*unwrapped, &[9; 32]);
    assert_eq!(logins.load(Ordering::SeqCst), 1);
    let debug = format!("{:?}", fixture.wrapper);
    assert!(!debug.contains("test-transit-token"));
    assert!(!debug.contains("test-workload-jwt"));
}

#[tokio::test]
async fn revoked_transit_tokens_force_authentication_on_the_next_request() {
    let logins = Arc::new(AtomicUsize::new(0));
    let router = Router::new()
        .route("/v1/auth/kubernetes/login", post(|State(logins): State<Arc<AtomicUsize>>| async move {
            let count = logins.fetch_add(1, Ordering::SeqCst);
            Json(json!({"auth": {"client_token": format!("token-{count}"), "lease_duration": 60}}))
        }))
        .route("/v1/transit/encrypt/payloads", post(|headers: HeaderMap| async move {
            if headers["X-Vault-Token"] == "token-0" {
                return StatusCode::FORBIDDEN.into_response();
            }
            Json(json!({"data": {"ciphertext": "vault:v1:d3JhcHBlZA=="}})).into_response()
        }))
        .with_state(logins.clone());
    let fixture = fixture(router).await;
    assert!(
        fixture
            .wrapper
            .wrap(&[9; 32], b"context")
            .await
            .is_err()
    );
    assert!(
        fixture
            .wrapper
            .wrap(&[9; 32], b"context")
            .await
            .is_ok()
    );
    assert_eq!(logins.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn transit_responses_have_a_size_limit_even_without_content_length() {
    let router = Router::new()
        .route("/sized", post(|| async { "x".repeat(65 * 1024) }))
        .route(
            "/chunked",
            post(|| async {
                Body::from_stream(futures_util::stream::iter([
                    Ok::<_, Infallible>(Bytes::from(vec![b'x'; 40 * 1024])),
                    Ok(Bytes::from(vec![b'x'; 40 * 1024])),
                ]))
            }),
        );
    let fixture = fixture(router).await;
    for path in ["sized", "chunked"] {
        let result: crate::error::AppResult<Value> = fixture
            .wrapper
            .post(path, None, &json!({}))
            .await;
        assert!(result.is_err(), "{path} must reject an oversized response");
    }
}

#[tokio::test]
async fn transit_does_not_follow_redirects_or_accept_invalid_key_sizes() {
    let router = Router::new()
        .route(
            "/v1/auth/kubernetes/login",
            post(|| async {
                Json(json!({"auth": {"client_token": "test-token", "lease_duration": 60}}))
            }),
        )
        .route(
            "/v1/transit/decrypt/payloads",
            post(|| async { Json(json!({"data": {"plaintext": STANDARD.encode([1u8; 16])}})) }),
        )
        .route(
            "/redirect",
            post(|| async { (StatusCode::TEMPORARY_REDIRECT, [("location", "/target")]) }),
        )
        .route("/target", post(|| async { Json(json!({"unexpected": true})) }));
    let fixture = fixture(router).await;
    let result: crate::error::AppResult<Value> = fixture
        .wrapper
        .post("redirect", None, &json!({}))
        .await;
    assert!(result.is_err());
    assert!(
        fixture
            .wrapper
            .unwrap("vault:v1:d3JhcHBlZA==", b"context")
            .await
            .is_err()
    );
}
