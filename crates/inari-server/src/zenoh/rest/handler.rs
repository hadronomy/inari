use axum::Router;
use axum::body::Bytes;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::Response;
use axum::routing::get;

use super::request::{QueryOptions, RequestMetadata};
use super::response::NegotiatedResponse;
use super::{ReadSelector, WriteSelector, ZenohRestService, index_response};
use crate::error::AppResult;
use crate::state::AppState;

pub(crate) fn router(_state: &AppState) -> Router<AppState> {
    Router::new()
        .route("/", get(index))
        .route(
            "/{*selector}",
            get(query)
                .post(query)
                .put(write)
                .patch(write)
                .delete(remove),
        )
}

async fn index(State(service): State<ZenohRestService>) -> Response {
    index_response(service.index())
}

async fn query(
    State(service): State<ZenohRestService>,
    selector: ReadSelector,
    options: QueryOptions,
    negotiated_response: NegotiatedResponse,
    metadata: RequestMetadata,
    body: Bytes,
) -> AppResult<Response> {
    service
        .query(&selector, options, negotiated_response, metadata, body)
        .await
}

async fn write(
    State(service): State<ZenohRestService>,
    selector: WriteSelector,
    metadata: RequestMetadata,
    body: Bytes,
) -> AppResult<StatusCode> {
    service
        .write(&selector, metadata, body)
        .await
}

async fn remove(
    State(service): State<ZenohRestService>,
    selector: WriteSelector,
) -> AppResult<StatusCode> {
    service.delete(&selector).await
}
