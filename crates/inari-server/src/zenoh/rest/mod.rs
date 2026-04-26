mod handler;
mod metadata;
mod request;
mod resolver;
mod response;
mod sse;

#[cfg(test)]
mod tests;

use std::time::Duration;

use axum::Json;
use axum::body::Bytes;
use axum::extract::FromRef;
use axum::response::{IntoResponse, Response};
pub(crate) use handler::router;
use serde::Serialize;
use zenoh::bytes::Encoding;
use zenoh::query::{Parameters, Reply, Selector};

use self::request::{LivelinessMode, QueryOptions, RequestMetadata, StandardQueryOptions};
pub(crate) use self::resolver::{ReadSelector, WriteSelector};
use self::response::{
    ApiBodyFormat, NegotiatedResponse, html_api_response, json_api_response, raw_zenoh_response,
};
use self::sse::sse_response;
use super::{KeyExpression, ZenohJsonSample, ZenohQueryRequest, ZenohStatus, reply_to_json_sample};
use crate::error::{AppError, AppResult};
use crate::state::AppState;

#[derive(Clone)]
pub(crate) struct ZenohRestService {
    state: AppState,
}

impl ZenohRestService {
    pub(crate) fn new(state: AppState) -> Self {
        Self { state }
    }

    pub(crate) fn index(&self) -> ZenohRestIndexResponse {
        let settings = &self.state.loaded_config().settings;

        ZenohRestIndexResponse {
            service: "zenoh_rest",
            enabled: settings.http.zenoh_rest.enabled,
            admin_space: ZenohRestAdminSpaceResponse {
                route_enabled: settings
                    .http
                    .zenoh_rest
                    .allow_admin_space,
                router_enabled: settings.zenoh.admin_space.enabled,
                read: settings.zenoh.admin_space.read,
                write: settings.zenoh.admin_space.write,
            },
            state: self.state.zenoh().status_snapshot(),
        }
    }

    pub(crate) async fn query(
        &self,
        key: &KeyExpression,
        options: QueryOptions,
        negotiated_response: NegotiatedResponse,
        metadata: RequestMetadata,
        body: Bytes,
    ) -> AppResult<Response> {
        let config = self
            .state
            .loaded_config()
            .settings
            .http
            .zenoh_rest
            .clone();

        match options {
            QueryOptions::Standard(standard) => match negotiated_response {
                NegotiatedResponse::EventStream => {
                    let subscription = self
                        .state
                        .zenoh()
                        .subscribe(key, config.sse_buffer.max(1))
                        .await?;

                    Ok(sse_response(subscription, config.sse_keep_alive))
                },
                NegotiatedResponse::Api(body_format) => {
                    let request = self.build_query_request(
                        key,
                        &standard,
                        metadata,
                        body,
                        config.query_timeout,
                    );

                    if standard.raw_response().is_raw() {
                        let reply = self
                            .state
                            .zenoh()
                            .query_first(request)
                            .await?;
                        return Ok(raw_zenoh_response(reply));
                    }

                    let replies = self
                        .state
                        .zenoh()
                        .query(request)
                        .await?;
                    Ok(render_replies(body_format, replies))
                },
            },

            QueryOptions::Liveliness(liveliness) => {
                self.ensure_empty_body(
                    &body,
                    "Zenoh liveliness requests do not accept query payloads.",
                )?;

                match negotiated_response {
                    NegotiatedResponse::EventStream => {
                        let history = matches!(liveliness.mode(), LivelinessMode::WithHistory);

                        let subscription = self
                            .state
                            .zenoh()
                            .subscribe_liveliness(key, config.sse_buffer.max(1), history)
                            .await?;

                        Ok(sse_response(subscription, config.sse_keep_alive))
                    },
                    NegotiatedResponse::Api(body_format) => {
                        if liveliness.raw_response().is_raw() {
                            let reply = self
                                .state
                                .zenoh()
                                .liveliness_query_first(key, config.query_timeout)
                                .await?;

                            return Ok(raw_zenoh_response(reply));
                        }

                        let replies = self
                            .state
                            .zenoh()
                            .liveliness_query(key, config.query_timeout)
                            .await?;

                        Ok(render_replies(body_format, replies))
                    },
                }
            },
        }
    }

    pub(crate) async fn write(
        &self,
        key: &KeyExpression,
        metadata: RequestMetadata,
        body: Bytes,
    ) -> AppResult<axum::http::StatusCode> {
        let transport = metadata.into_transport(Encoding::APPLICATION_OCTET_STREAM);

        self.state
            .zenoh()
            .put_bytes(key.clone(), body, transport.encoding, transport.attachment)
            .await?;

        Ok(axum::http::StatusCode::OK)
    }

    pub(crate) async fn delete(&self, key: &KeyExpression) -> AppResult<axum::http::StatusCode> {
        self.state
            .zenoh()
            .delete(key.clone())
            .await?;

        Ok(axum::http::StatusCode::OK)
    }

    fn build_query_request(
        &self,
        key: &KeyExpression,
        options: &StandardQueryOptions,
        metadata: RequestMetadata,
        body: Bytes,
        timeout: Duration,
    ) -> ZenohQueryRequest {
        let parameters = Parameters::from(
            options
                .selector_query()
                .unwrap_or_default()
                .to_owned(),
        );
        let selector = Selector::owned(key.clone(), parameters);

        let mut request = ZenohQueryRequest::new(selector, timeout, options.consolidation().into());

        if !body.is_empty() {
            let transport = metadata.into_transport(Encoding::default());
            request = request.with_payload(body, transport.encoding, transport.attachment);
        }

        request
    }

    fn ensure_empty_body(&self, body: &Bytes, message: &'static str) -> AppResult<()> {
        if body.is_empty() { Ok(()) } else { Err(AppError::bad_request(message)) }
    }
}

impl FromRef<AppState> for ZenohRestService {
    fn from_ref(state: &AppState) -> Self {
        Self::new(state.clone())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct ZenohRestIndexResponse {
    service: &'static str,
    enabled: bool,
    admin_space: ZenohRestAdminSpaceResponse,
    state: ZenohStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct ZenohRestAdminSpaceResponse {
    route_enabled: bool,
    router_enabled: bool,
    read: bool,
    write: bool,
}

fn render_replies(body_format: ApiBodyFormat, replies: Vec<Reply>) -> Response {
    match body_format {
        ApiBodyFormat::Html => html_api_response(&replies),
        ApiBodyFormat::Json => {
            let samples = replies
                .iter()
                .map(reply_to_json_sample)
                .collect::<Vec<ZenohJsonSample>>();

            json_api_response(samples)
        },
    }
}

fn index_response(index: ZenohRestIndexResponse) -> Response {
    Json(index).into_response()
}
