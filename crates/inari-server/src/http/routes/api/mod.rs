mod extract;
mod inari;
mod zenoh;

use axum::Router;
use axum::extract::OriginalUri;

use crate::error::AppError;
use crate::state::AppState;

pub fn router(state: &AppState) -> Router<AppState> {
    let router = Router::new().nest("/inari/v1", inari::router());

    let router = if state
        .loaded_config()
        .settings
        .http
        .zenoh_rest
        .enabled
    {
        router.nest("/zenoh/v1", zenoh::router(state))
    } else {
        router
    };

    router.fallback(api_not_found)
}

async fn api_not_found(OriginalUri(uri): OriginalUri) -> AppError {
    AppError::not_found(format!("No API resource exists at `{}`.", uri.path()))
}
