use axum::Router;

use crate::{state::AppState, zenoh::rest};

pub fn router(state: &AppState) -> Router<AppState> {
    rest::router(state)
}
