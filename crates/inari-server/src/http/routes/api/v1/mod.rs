pub mod protocol;
pub mod zenoh;

use axum::{Json, Router, extract::State, routing::get};
use serde::Serialize;

use crate::{state::AppState, zenoh::ZenohStatus};

pub fn router(state: &AppState) -> Router<AppState> {
    let router = Router::new().route("/", get(index)).nest("/protocol", protocol::router(state));

    if state.loaded_config().settings.http.zenoh_rest.enabled {
        router.nest("/zenoh", zenoh::router(state))
    } else {
        router
    }
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
