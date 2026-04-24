pub mod routes;

use axum::Router;
use axum::error_handling::HandleErrorLayer;
use axum::extract::DefaultBodyLimit;
use axum::http::header::{AUTHORIZATION, COOKIE};
use axum::http::{HeaderName, HeaderValue, Method};
use axum::response::IntoResponse;
use tower::timeout::TimeoutLayer;
use tower::{BoxError, ServiceBuilder};
use tower_http::catch_panic::CatchPanicLayer;
use tower_http::compression::CompressionLayer;
use tower_http::cors::{AllowOrigin, CorsLayer};
use tower_http::request_id::{MakeRequestUuid, PropagateRequestIdLayer, SetRequestIdLayer};
use tower_http::sensitive_headers::SetSensitiveHeadersLayer;
use tower_http::trace::{DefaultOnFailure, DefaultOnRequest, DefaultOnResponse, TraceLayer};
use tracing::Level;

use crate::config::CorsConfig;
use crate::error::{AppError, AppResult, ConfigError};
use crate::state::AppState;

pub fn router(state: &AppState) -> AppResult<Router<AppState>> {
    let cors = state
        .loaded_config()
        .settings
        .http
        .cors
        .enabled
        .then(|| build_cors_layer(&state.loaded_config().settings.http.cors))
        .transpose()?;

    let router = Router::new()
        .merge(routes::system::router())
        .merge(routes::api::router(state));

    Ok(apply_http_layers(router, state, cors))
}

async fn handle_middleware_error(error: BoxError) -> impl IntoResponse {
    AppError::from_box_error(error).log_for_server()
}

fn apply_http_layers(
    router: Router<AppState>,
    state: &AppState,
    cors: Option<CorsLayer>,
) -> Router<AppState> {
    router
        .layer(DefaultBodyLimit::max(
            state
                .loaded_config()
                .settings
                .server
                .max_body_size_bytes,
        ))
        .layer(
            ServiceBuilder::new()
                .layer(HandleErrorLayer::new(handle_middleware_error))
                .layer(TimeoutLayer::new(
                    state
                        .loaded_config()
                        .settings
                        .server
                        .request_timeout,
                ))
                .layer(SetSensitiveHeadersLayer::new([AUTHORIZATION, COOKIE]))
                .layer(PropagateRequestIdLayer::x_request_id())
                .layer(SetRequestIdLayer::x_request_id(MakeRequestUuid))
                .layer(
                    TraceLayer::new_for_http()
                        .on_request(DefaultOnRequest::new().level(Level::DEBUG))
                        .on_response(DefaultOnResponse::new().level(Level::INFO))
                        .on_failure(DefaultOnFailure::new().level(Level::WARN)),
                )
                .layer(CatchPanicLayer::new())
                .layer(CompressionLayer::new())
                .option_layer(cors),
        )
}

fn build_cors_layer(config: &CorsConfig) -> Result<CorsLayer, ConfigError> {
    if config.allow_credentials && config.allow_origins.is_empty() {
        return Err(ConfigError::invalid(
            "CORS allow_credentials requires at least one explicit allow_origin.",
        ));
    }

    let methods = config
        .allow_methods
        .iter()
        .map(|value| {
            value
                .parse::<Method>()
                .map_err(|source| {
                    ConfigError::invalid(format!("Invalid CORS method `{value}`."))
                        .with_source(source)
                })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let headers = config
        .allow_headers
        .iter()
        .map(|value| {
            value
                .parse::<HeaderName>()
                .map_err(|source| {
                    ConfigError::invalid(format!("Invalid CORS header `{value}`."))
                        .with_source(source)
                })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let expose_headers = config
        .expose_headers
        .iter()
        .map(|value| {
            value
                .parse::<HeaderName>()
                .map_err(|source| {
                    ConfigError::invalid(format!("Invalid exposed CORS header `{value}`."))
                        .with_source(source)
                })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let origin_layer = if config.allow_origins.is_empty() {
        AllowOrigin::any()
    } else {
        AllowOrigin::list(
            config
                .allow_origins
                .iter()
                .map(|value| {
                    value
                        .parse::<HeaderValue>()
                        .map_err(|source| {
                            ConfigError::invalid(format!("Invalid CORS origin `{value}`."))
                                .with_source(source)
                        })
                })
                .collect::<Result<Vec<_>, _>>()?,
        )
    };

    let cors = CorsLayer::new()
        .allow_origin(origin_layer)
        .allow_methods(methods)
        .allow_headers(headers)
        .expose_headers(expose_headers)
        .max_age(config.max_age);

    Ok(if config.allow_credentials { cors.allow_credentials(true) } else { cors })
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use axum::body::{Body, to_bytes};
    use axum::http::{Request, StatusCode};
    use serde_json::Value;
    use tower::ServiceExt;

    use super::router;
    use crate::config::{LoadedConfig, ZenohConfig};
    use crate::protocol::NoopProtocolModule;
    use crate::state::AppState;
    use crate::zenoh::ZenohSupervisor;

    fn test_app() -> axum::Router {
        let loaded = LoadedConfig::default();
        let (zenoh, _) = ZenohSupervisor::new(ZenohConfig::default());
        let state = AppState::new(loaded, zenoh, Arc::new(NoopProtocolModule));

        router(&state)
            .expect("test router should build")
            .with_state(state)
    }

    #[tokio::test]
    async fn healthz_returns_ok() {
        let response = test_app()
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .body(Body::empty())
                    .expect("request should be valid"),
            )
            .await
            .expect("router should respond");

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn readyz_reports_ready_when_zenoh_is_disabled() {
        let response = test_app()
            .oneshot(
                Request::builder()
                    .uri("/readyz")
                    .body(Body::empty())
                    .expect("request should be valid"),
            )
            .await
            .expect("router should respond");

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn protocol_stub_returns_uniform_error_shape() {
        let response = test_app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/v1/protocol/commands")
                    .body(Body::empty())
                    .expect("request should be valid"),
            )
            .await
            .expect("router should respond");

        assert_eq!(response.status(), StatusCode::NOT_IMPLEMENTED);

        let body = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("response body should be readable");
        let payload: Value = serde_json::from_slice(&body).expect("response body should be JSON");

        assert_eq!(payload["error"]["code"], "not_implemented");
    }
}
