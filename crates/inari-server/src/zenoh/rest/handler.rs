use axum::body::{Body, Bytes};
use axum::extract::{Request, State};
use axum::http::StatusCode;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Router, middleware};

use super::negotiation::NegotiatedResponse;
use super::request::{QueryOptions, RequestMetadata};
use super::{ReadSelector, WriteSelector, ZenohRestService, index_response};
use crate::error::AppResult;
use crate::state::AppState;

pub(crate) fn router(state: &AppState) -> Router<AppState> {
    Router::new()
        .route("/", get(index))
        .route(
            "/{*selector}",
            get(query)
                .post(query)
                .put(write)
                .patch(write)
                .delete(remove)
                .route_layer(middleware::from_fn_with_state(
                    state.clone(),
                    shed_excess_zenoh_rest_requests,
                )),
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

async fn shed_excess_zenoh_rest_requests(
    State(state): State<AppState>,
    request: Request<Body>,
    next: Next,
) -> Response {
    let Ok(_permit) = state.try_acquire_zenoh_rest_requests_permit() else {
        return (StatusCode::SERVICE_UNAVAILABLE, [("retry-after", "1")], "busy\n").into_response();
    };

    next.run(request).await
}
