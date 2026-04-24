use axum::{
    Router,
    body::Bytes,
    extract::{Path, RawQuery, State},
    http::{HeaderMap, StatusCode},
    response::Response,
    routing::get,
};

use crate::{error::AppResult, state::AppState};

use super::{ZenohRestService, index_response};

pub(crate) fn router(_state: &AppState) -> Router<AppState> {
    Router::new()
        .route(
            "/",
            get(index)
                .post(empty_selector_query)
                .put(empty_selector_write)
                .patch(empty_selector_write)
                .delete(empty_selector_write),
        )
        .route("/{*selector}", get(query).post(query).put(write).patch(write).delete(remove))
}

async fn index(State(state): State<AppState>) -> Response {
    index_response(ZenohRestService::new(state).index())
}

async fn query(
    State(state): State<AppState>,
    Path(selector): Path<String>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
    body: Bytes,
) -> AppResult<Response> {
    ZenohRestService::new(state).query(&selector, query, headers, body).await
}

async fn write(
    State(state): State<AppState>,
    Path(selector): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> AppResult<StatusCode> {
    ZenohRestService::new(state).write(&selector, &headers, body).await
}

async fn remove(
    State(state): State<AppState>,
    Path(selector): Path<String>,
) -> AppResult<StatusCode> {
    ZenohRestService::new(state).delete(&selector).await
}

async fn empty_selector_query(State(state): State<AppState>) -> AppResult<Response> {
    ZenohRestService::new(state).empty_selector_response()
}

async fn empty_selector_write(State(state): State<AppState>) -> AppResult<StatusCode> {
    ZenohRestService::new(state).empty_selector_response()
}
