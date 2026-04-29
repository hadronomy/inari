use std::sync::Arc;
use std::time::Duration;

use axum::body::{Body, BodyDataStream, to_bytes};
use axum::http::{Request, StatusCode};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio_stream::StreamExt;
use tower::ServiceExt;
use zenoh::bytes::Encoding;
use zenoh::liveliness::LivelinessToken;
use zenoh::sample::SampleKind;

use crate::ConcurrencyLimit;
use crate::config::{LoadedConfig, ZenohAdminSpaceConfig, ZenohConfig};
use crate::http;
use crate::protocol::NoopProtocolModule;
use crate::shutdown::{ShutdownCoordinator, ShutdownReason};
use crate::state::AppState;
use crate::zenoh::ZenohSupervisor;

fn limit(value: usize) -> ConcurrencyLimit {
    value
        .try_into()
        .expect("test concurrency limit should be non-zero")
}

fn base_config() -> LoadedConfig {
    let mut loaded = LoadedConfig::default();

    loaded
        .settings
        .protocol
        .max_concurrent_requests = limit(8);
    loaded
        .settings
        .http
        .zenoh_rest
        .max_concurrent_requests = limit(8);

    loaded
}

fn enabled_config() -> LoadedConfig {
    let mut loaded = base_config();

    loaded.settings.http.zenoh_rest.enabled = true;
    loaded.settings.zenoh.enabled = true;
    loaded.settings.zenoh.listen_endpoints = vec!["tcp/127.0.0.1:0".into()];

    loaded
}

fn admin_enabled_config() -> LoadedConfig {
    let mut loaded = enabled_config();

    loaded
        .settings
        .http
        .zenoh_rest
        .allow_admin_space = true;
    loaded.settings.zenoh.admin_space =
        ZenohAdminSpaceConfig { enabled: true, read: true, write: false };

    loaded
}

struct TestApp {
    router: axum::Router,
    state: AppState,
    shutdown: Option<ShutdownCoordinator>,
    task: Option<JoinHandle<crate::AppResult<()>>>,
}

impl TestApp {
    async fn spawn(loaded: LoadedConfig) -> Self {
        let zenoh_settings = loaded.settings.zenoh.clone();
        let zenoh_enabled = loaded.settings.zenoh.enabled;

        let (zenoh, supervisor) = ZenohSupervisor::new(zenoh_settings);

        let state = AppState::new(loaded, zenoh, Arc::new(NoopProtocolModule));

        let shutdown = ShutdownCoordinator::new(Duration::from_secs(1));
        let task = tokio::spawn(supervisor.run(shutdown.clone()));

        if zenoh_enabled {
            wait_for_connection(&state).await;
        }

        let router = http::router(&state)
            .expect("router should build")
            .with_state(state.clone());

        Self { router, state, shutdown: Some(shutdown), task: Some(task) }
    }

    async fn request(&self, request: Request<Body>) -> axum::response::Response {
        self.router
            .clone()
            .oneshot(request)
            .await
            .expect("router should respond")
    }

    fn state(&self) -> &AppState {
        &self.state
    }

    async fn shutdown(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            shutdown.request(ShutdownReason::ServerStopped);
        }

        if let Some(task) = self.task.take() {
            task.await
                .expect("supervisor task should join")
                .expect("supervisor should shut down");
        }
    }
}

impl Drop for TestApp {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            shutdown.request(ShutdownReason::ServerStopped);
        }

        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

async fn wait_for_connection(state: &AppState) {
    for _ in 0..100 {
        if state
            .zenoh()
            .session_snapshot()
            .is_some()
        {
            return;
        }

        tokio::time::sleep(Duration::from_millis(50)).await;
    }

    panic!("Zenoh session did not connect in time: {:?}", state.zenoh().status_snapshot());
}

struct TestTasks(Vec<JoinHandle<()>>);

impl Drop for TestTasks {
    fn drop(&mut self) {
        for task in self.0.drain(..) {
            task.abort();
        }
    }
}

async fn declare_liveliness_token(state: &AppState, key: &'static str) -> LivelinessToken {
    state
        .zenoh()
        .session_snapshot()
        .expect("session should be connected")
        .session()
        .liveliness()
        .declare_token(key)
        .await
        .expect("liveliness token should declare")
}

async fn install_materialized_queryable(state: &AppState, key: &'static str) -> TestTasks {
    let session = state
        .zenoh()
        .session_snapshot()
        .expect("session should be connected")
        .session()
        .clone();

    let value = Arc::new(Mutex::new(None::<(Vec<u8>, Encoding)>));

    let subscriber = session
        .declare_subscriber(key)
        .await
        .expect("subscriber should declare");

    let queryable = session
        .declare_queryable(key)
        .await
        .expect("queryable should declare");

    let subscriber_value = Arc::clone(&value);

    let subscriber_task = tokio::spawn(async move {
        while let Ok(sample) = subscriber.recv_async().await {
            let mut guard = subscriber_value.lock().await;

            match sample.kind() {
                SampleKind::Put => {
                    *guard =
                        Some((sample.payload().to_bytes().into_owned(), sample.encoding().clone()));
                },
                SampleKind::Delete => {
                    *guard = None;
                },
            }
        }
    });

    let query_task = tokio::spawn(async move {
        while let Ok(query) = queryable.recv_async().await {
            let reply = value.lock().await.clone();

            if let Some((payload, encoding)) = reply {
                let result = query
                    .reply(key, payload)
                    .encoding(encoding)
                    .await;
                assert!(result.is_ok(), "query reply should succeed");
            }
        }
    });

    TestTasks(vec![subscriber_task, query_task])
}

async fn install_echo_queryable(state: &AppState, key: &'static str) -> TestTasks {
    let session = state
        .zenoh()
        .session_snapshot()
        .expect("session should be connected")
        .session()
        .clone();

    let queryable = session
        .declare_queryable(key)
        .await
        .expect("queryable should declare");

    let query_task = tokio::spawn(async move {
        while let Ok(query) = queryable.recv_async().await {
            let payload = query
                .payload()
                .map(|payload| payload.to_bytes().into_owned())
                .unwrap_or_default();

            let encoding = query
                .encoding()
                .cloned()
                .unwrap_or_default();

            let result = query
                .reply(key, payload)
                .encoding(encoding)
                .await;

            assert!(result.is_ok(), "query reply should succeed");
        }
    });

    TestTasks(vec![query_task])
}

async fn read_sse_event(stream: &mut BodyDataStream) -> String {
    let mut frame = Vec::new();

    loop {
        let chunk = tokio::time::timeout(Duration::from_secs(2), stream.next())
            .await
            .expect("SSE event should arrive in time")
            .expect("SSE stream should remain open")
            .expect("SSE body chunk should be readable");

        frame.extend_from_slice(&chunk);

        let text = String::from_utf8_lossy(&frame);

        if text.contains("\n\n") || text.contains("\r\n\r\n") {
            return text.into_owned();
        }
    }
}

#[tokio::test]
async fn route_is_not_mounted_when_disabled() {
    let loaded = base_config();

    let (zenoh, _) = ZenohSupervisor::new(ZenohConfig::default());

    let state = AppState::new(loaded, zenoh, Arc::new(NoopProtocolModule));

    let app = http::router(&state)
        .expect("router should build")
        .with_state(state);

    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/v1/zenoh")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await
        .expect("router should respond");

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn zenoh_root_reports_connection_state() {
    let mut app = TestApp::spawn(enabled_config()).await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::OK);

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn zenoh_admin_space_is_blocked_by_default() {
    let mut app = TestApp::spawn(enabled_config()).await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/@/local/router")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn invalid_key_expression_returns_bad_request() {
    let mut app = TestApp::spawn(enabled_config()).await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/demo/example/*eval")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn zenoh_admin_space_returns_router_status_when_enabled() {
    let mut app = TestApp::spawn(admin_enabled_config()).await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/@/local/router")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::OK);

    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body should be readable");

    let json: serde_json::Value =
        serde_json::from_slice(&body).expect("response body should be JSON");

    assert!(
        json.as_array()
            .is_some_and(|items| !items.is_empty())
    );

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn put_patch_get_delete_round_trip_through_http_surface() {
    let mut app = TestApp::spawn(enabled_config()).await;
    let _queryable = install_materialized_queryable(app.state(), "demo/materialized").await;

    let put = app
        .request(
            Request::builder()
                .method("PUT")
                .uri("/api/v1/zenoh/demo/materialized")
                .header("content-type", "text/plain")
                .body(Body::from("hello"))
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(put.status(), StatusCode::OK);

    tokio::time::sleep(Duration::from_millis(100)).await;

    let patch = app
        .request(
            Request::builder()
                .method("PATCH")
                .uri("/api/v1/zenoh/demo/materialized")
                .header("content-type", "text/plain")
                .body(Body::from("patched"))
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(patch.status(), StatusCode::OK);

    tokio::time::sleep(Duration::from_millis(100)).await;

    let get = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/demo/materialized")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(get.status(), StatusCode::OK);

    let body = to_bytes(get.into_body(), usize::MAX)
        .await
        .expect("response body should be readable");

    let json: serde_json::Value =
        serde_json::from_slice(&body).expect("response body should be JSON");

    assert_eq!(json[0]["value"], "patched");

    let delete = app
        .request(
            Request::builder()
                .method("DELETE")
                .uri("/api/v1/zenoh/demo/materialized")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(delete.status(), StatusCode::OK);

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn post_query_forwards_payload_and_encoding() {
    let mut app = TestApp::spawn(enabled_config()).await;
    let _queryable = install_echo_queryable(app.state(), "demo/echo").await;

    let response = app
        .request(
            Request::builder()
                .method("POST")
                .uri("/api/v1/zenoh/demo/echo")
                .header("content-type", "text/plain")
                .body(Body::from("hello from query"))
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::OK);

    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body should be readable");

    let json: serde_json::Value =
        serde_json::from_slice(&body).expect("response body should be JSON");

    assert_eq!(json[0]["value"], "hello from query");
    assert_eq!(json[0]["encoding"], "text/plain");

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn get_raw_returns_first_reply_payload() {
    let mut app = TestApp::spawn(enabled_config()).await;
    let _queryable = install_echo_queryable(app.state(), "demo/raw").await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/demo/raw?_raw")
                .header("content-type", "text/plain")
                .body(Body::from("raw payload"))
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::OK);

    assert_eq!(
        response
            .headers()
            .get("content-type")
            .expect("content-type should exist"),
        "text/plain",
    );

    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body should be readable");

    assert_eq!(body, "raw payload");

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn get_html_renders_definition_list() {
    let mut app = TestApp::spawn(enabled_config()).await;
    let _queryable = install_echo_queryable(app.state(), "demo/html").await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/demo/html")
                .header("accept", "text/html")
                .header("content-type", "text/plain")
                .body(Body::from("<hello>"))
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::OK);

    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body should be readable");

    let html = String::from_utf8(body.to_vec()).expect("response body should be valid UTF-8");

    assert!(html.contains("<dl>"));
    assert!(html.contains("&lt;hello&gt;"));

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn get_liveliness_returns_live_tokens() {
    let mut app = TestApp::spawn(enabled_config()).await;
    let token = declare_liveliness_token(app.state(), "demo/presence").await;

    tokio::time::sleep(Duration::from_millis(100)).await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/demo/presence?_liveliness")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::OK);

    let body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body should be readable");

    let json: serde_json::Value =
        serde_json::from_slice(&body).expect("response body should be JSON");

    assert_eq!(json[0]["key"], "demo/presence");
    assert_eq!(json[0]["value"], serde_json::Value::Null);

    drop(token);

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn liveliness_sse_stream_reports_history_and_drop() {
    let mut app = TestApp::spawn(enabled_config()).await;
    let token = declare_liveliness_token(app.state(), "demo/presence").await;

    tokio::time::sleep(Duration::from_millis(100)).await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/demo/presence?_liveliness&_history")
                .header("accept", "text/event-stream")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::OK);

    let mut stream = response.into_body().into_data_stream();

    let initial = read_sse_event(&mut stream).await;
    assert!(initial.contains("event: PUT"));
    assert!(initial.contains("\"key\":\"demo/presence\""));
    assert!(initial.contains("\"value\":null"));

    drop(token);

    let dropped = read_sse_event(&mut stream).await;
    assert!(dropped.contains("event: DELETE"));
    assert!(dropped.contains("\"key\":\"demo/presence\""));

    app.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn liveliness_rejects_non_reserved_selector_parameters() {
    let mut app = TestApp::spawn(enabled_config()).await;

    let response = app
        .request(
            Request::builder()
                .uri("/api/v1/zenoh/demo/presence?_liveliness&foo=bar")
                .body(Body::empty())
                .expect("request should be valid"),
        )
        .await;

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    app.shutdown().await;
}
