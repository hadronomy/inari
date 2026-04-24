use axum::{Json, Router, extract::State, http::StatusCode, routing::get};
use serde::Serialize;

use crate::state::{AppState, ReadinessSnapshot};

pub fn router() -> Router<AppState> {
    Router::new().route("/healthz", get(health)).route("/readyz", get(readiness))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct HealthResponse {
    service: &'static str,
    version: &'static str,
    started_at: chrono::DateTime<chrono::Utc>,
    uptime_secs: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ReadinessResponse {
    service: &'static str,
    version: &'static str,
    readiness: ReadinessSnapshot,
}

async fn health(State(state): State<AppState>) -> Json<HealthResponse> {
    Json(HealthResponse {
        service: env!("CARGO_PKG_NAME"),
        version: env!("CARGO_PKG_VERSION"),
        started_at: state.started_at(),
        uptime_secs: state.uptime().as_secs(),
    })
}

async fn readiness(State(state): State<AppState>) -> (StatusCode, Json<ReadinessResponse>) {
    let readiness = state.readiness_snapshot();
    let status = if readiness.ready { StatusCode::OK } else { StatusCode::SERVICE_UNAVAILABLE };

    (
        status,
        Json(ReadinessResponse {
            service: env!("CARGO_PKG_NAME"),
            version: env!("CARGO_PKG_VERSION"),
            readiness,
        }),
    )
}
