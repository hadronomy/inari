pub mod protocol;

use axum::extract::State;
use axum::routing::get;
use axum::{Json, Router};
use serde::Serialize;

use crate::state::AppState;
use crate::zenoh::ZenohStatus;

pub fn router(state: &AppState) -> Router<AppState> {
    Router::new()
        .route("/", get(index))
        .nest("/protocol", protocol::router(state))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ApiIndexResponse {
    service: &'static str,
    version: &'static str,
    protocol: crate::protocol::ProtocolDescriptor,
    zenoh: ZenohStatus,
}

async fn index(State(state): State<AppState>) -> Json<ApiIndexResponse> {
    Json(ApiIndexResponse {
        service: env!("CARGO_PKG_NAME"),
        version: "v1",
        protocol: state.protocol_descriptor(),
        zenoh: state.zenoh().status_snapshot(),
    })
}
