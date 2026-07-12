pub mod routes;
mod ssr_render;

use axum::Router;
use axum::error_handling::HandleErrorLayer;
use axum::extract::DefaultBodyLimit;
use axum::http::header::{AUTHORIZATION, COOKIE};
use axum::http::{HeaderName, HeaderValue, Method};
use axum::middleware;
use axum::response::IntoResponse;
use leptos::prelude::provide_context;
use leptos::reactive::diagnostics::SpecialNonReactiveZone;
use leptos_axum::{
    AxumRouteListing, ErrorHandler, LeptosRoutes, generate_route_list, site_pkg_dir_service,
};
use tower::timeout::TimeoutLayer;
use tower::{BoxError, ServiceBuilder};
use tower_http::catch_panic::CatchPanicLayer;
use tower_http::compression::CompressionLayer;
use tower_http::cors::{AllowOrigin, CorsLayer};
use tower_http::request_id::{MakeRequestUuid, PropagateRequestIdLayer, SetRequestIdLayer};
use tower_http::sensitive_headers::SetSensitiveHeadersLayer;
use tower_http::trace::{DefaultOnFailure, DefaultOnRequest, DefaultOnResponse, TraceLayer};
use tower_sessions::cookie::SameSite;
use tower_sessions::{Expiry, SessionManagerLayer};
use tracing::Level;

use crate::config::CorsConfig;
use crate::error::{AppError, AppResult, ConfigError};
use crate::state::AppState;

pub fn router(state: &AppState) -> AppResult<Router<AppState>> {
    let settings = &state.loaded_config().settings;
    let cors = settings
        .http
        .cors
        .enabled
        .then(|| build_cors_layer(&settings.http.cors))
        .transpose()?;

    let onboarding = inari_web::OnboardingContext::from(state.onboarding().cloned());
    let leptos_options = state.leptos_options().clone();
    let web_routes = discover_web_routes();
    let provide_onboarding_context = move || {
        provide_context(onboarding.clone());
    };
    let site_service =
        site_pkg_dir_service(&leptos_options).fallback(ErrorHandler::new_with_context(
            provide_onboarding_context.clone(),
            inari_web::shell,
            leptos_options.clone(),
        ));
    let mut router = Router::new()
        .merge(routes::system::router())
        .nest("/api", routes::api::router(state))
        .leptos_routes_with_context(state, web_routes, provide_onboarding_context, {
            let leptos_options = leptos_options.clone();
            move || inari_web::shell(leptos_options.clone())
        })
        .fallback_service(site_service)
        .layer(middleware::from_fn(ssr_render::serve));

    if let Some(identity) = state.identity() {
        let secure_cookie = settings.server.production
            || settings
                .server
                .public_url
                .as_ref()
                .is_some_and(|url| url.scheme() == "https");
        let sessions = SessionManagerLayer::new(identity.sessions().clone())
            .with_name("inari_session")
            .with_http_only(true)
            .with_same_site(SameSite::Lax)
            .with_secure(secure_cookie)
            .with_path("/")
            .with_expiry(Expiry::OnInactivity(time::Duration::hours(8)));
        router = router
            .merge(routes::auth::router())
            .layer(sessions);
    }

    Ok(apply_http_layers(router, state, cors))
}

fn discover_web_routes() -> Vec<AxumRouteListing> {
    let _non_reactive = SpecialNonReactiveZone::enter();
    generate_route_list(inari_web::App)
}

async fn handle_middleware_error(error: BoxError) -> impl IntoResponse {
    AppError::from_box_error(error).log_for_server()
}

fn apply_http_layers(
    router: Router<AppState>,
    state: &AppState,
    cors: Option<CorsLayer>,
) -> Router<AppState> {
    let settings = &state.loaded_config().settings;
    router
        .layer(DefaultBodyLimit::max(settings.server.max_body_size_bytes))
        .layer(
            ServiceBuilder::new()
                .layer(HandleErrorLayer::new(handle_middleware_error))
                .layer(TimeoutLayer::new(settings.server.request_timeout))
                .layer(SetSensitiveHeadersLayer::new([AUTHORIZATION, COOKIE]))
                .layer(PropagateRequestIdLayer::x_request_id())
                .layer(SetRequestIdLayer::x_request_id(MakeRequestUuid))
                .layer(
                    TraceLayer::new_for_http()
                        .on_request(DefaultOnRequest::new().level(Level::DEBUG))
                        .on_response(DefaultOnResponse::new().level(Level::DEBUG))
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
    use axum::body::{Body, to_bytes};
    use axum::http::{Request, StatusCode};
    use serde_json::Value;
    use tower::ServiceExt;

    use super::router;
    use crate::ConcurrencyLimit;
    use crate::config::{LoadedConfig, ZenohConfig};
    use crate::state::AppState;
    use crate::zenoh::ZenohSupervisor;

    fn limit(value: usize) -> ConcurrencyLimit {
        value
            .try_into()
            .expect("test concurrency limit should be non-zero")
    }

    fn test_app() -> axum::Router {
        let mut loaded = LoadedConfig::default();

        loaded
            .settings
            .http
            .inari_api
            .max_concurrent_requests = limit(8);
        loaded
            .settings
            .http
            .zenoh_rest
            .max_concurrent_requests = limit(8);

        let (zenoh, _) = ZenohSupervisor::new(ZenohConfig::default());

        let state = AppState::new(loaded, zenoh);

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
    async fn removed_protocol_route_returns_problem_details() {
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

        assert_eq!(response.status(), StatusCode::NOT_FOUND);

        let body = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("response body should be readable");

        let payload: Value = serde_json::from_slice(&body).expect("response body should be JSON");

        assert_eq!(payload["type"], "urn:inari:problem:not_found");
        assert_eq!(payload["title"], "Not Found");
        assert_eq!(payload["status"], 404);
        assert_eq!(payload["code"], "not_found");
    }

    #[tokio::test]
    async fn unknown_api_routes_never_fall_through_to_leptos() {
        let response = test_app()
            .oneshot(
                Request::builder()
                    .uri("/api/does-not-exist")
                    .body(Body::empty())
                    .expect("request should be valid"),
            )
            .await
            .expect("router should respond");

        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            response
                .headers()
                .get("content-type")
                .unwrap(),
            "application/problem+json"
        );
        let body = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("response body should be readable");
        let payload: Value = serde_json::from_slice(&body).expect("response body should be JSON");
        assert_eq!(payload["code"], "not_found");
        assert_eq!(payload["detail"], "No API resource exists at `/api/does-not-exist`.");
    }

    #[tokio::test]
    async fn setup_route_renders_a_typed_disabled_state_without_panicking() {
        let response = test_app()
            .oneshot(
                Request::builder()
                    .uri("/setup/ABCDEFGH2345")
                    .body(Body::empty())
                    .expect("request should be valid"),
            )
            .await
            .expect("router should respond");

        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        let body = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("response body should be readable");
        let html = String::from_utf8(body.to_vec()).expect("response body should be UTF-8");
        assert!(html.contains("This connection cannot be started."));
        assert!(html.contains("Managed onboarding is not enabled"));
    }
}
