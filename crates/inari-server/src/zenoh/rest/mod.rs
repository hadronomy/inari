mod handler;
mod resolver;
mod response;
mod sse;

#[cfg(test)]
mod tests;

use std::time::Duration;

use axum::{
    Json,
    body::Bytes,
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use serde::Serialize;

use crate::{
    error::{AppError, AppResult},
    state::AppState,
};

use self::{
    resolver::{AdminOperation, KeyResolver},
    response::{ResponseKind, html_response, json_response, preferred_response_kind, raw_response},
    sse::sse_response,
};
use super::{KeyExpression, ZenohJsonSample, ZenohQueryRequest, reply_to_json_sample};

pub(crate) use handler::router;

const RAW_KEY: &str = "_raw";
const LIVELINESS_KEY: &str = "_liveliness";
const HISTORY_KEY: &str = "_history";
const EMPTY_SELECTOR_MESSAGE: &str = "Zenoh key expression cannot be empty.";

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
                route_enabled: settings.http.zenoh_rest.allow_admin_space,
                router_enabled: settings.zenoh.admin_space.enabled,
                read: settings.zenoh.admin_space.read,
                write: settings.zenoh.admin_space.write,
            },
            state: self.state.zenoh().status_snapshot(),
        }
    }

    pub(crate) async fn query(
        &self,
        selector: &str,
        query: Option<String>,
        headers: HeaderMap,
        body: Bytes,
    ) -> AppResult<Response> {
        let key = self.resolve_key_expression(selector, AdminOperation::Read)?;
        let config = self.state.loaded_config().settings.http.zenoh_rest.clone();
        let response_kind = preferred_response_kind(&headers);
        let options = QueryOptions::parse(query)?;

        if response_kind == ResponseKind::EventStream {
            let buffer = config.sse_buffer.max(1);
            let subscription = match options.mode {
                QueryMode::Standard => self.state.zenoh().subscribe(&key, buffer).await?,
                QueryMode::Liveliness { history } => {
                    self.ensure_empty_body(
                        &body,
                        "Zenoh liveliness requests do not accept query payloads.",
                    )?;
                    self.state.zenoh().subscribe_liveliness(&key, buffer, history).await?
                }
            };
            return Ok(sse_response(subscription, config.sse_keep_alive, buffer));
        }

        match options.mode {
            QueryMode::Standard => {
                let resolved = self.build_query_request(
                    key,
                    options.query,
                    &headers,
                    body,
                    config.query_timeout,
                );

                if resolved.raw {
                    let reply = self.state.zenoh().query_first(resolved.request).await?;
                    return Ok(raw_response(reply));
                }

                let replies = self.state.zenoh().query(resolved.request).await?;
                Ok(render_replies(response_kind, replies))
            }
            QueryMode::Liveliness { .. } => {
                self.ensure_empty_body(
                    &body,
                    "Zenoh liveliness requests do not accept query payloads.",
                )?;

                if options.raw {
                    let reply = self
                        .state
                        .zenoh()
                        .liveliness_query_first(&key, config.query_timeout)
                        .await?;
                    return Ok(raw_response(reply));
                }

                let replies =
                    self.state.zenoh().liveliness_query(&key, config.query_timeout).await?;
                Ok(render_replies(response_kind, replies))
            }
        }
    }

    pub(crate) async fn write(
        &self,
        selector: &str,
        headers: &HeaderMap,
        body: Bytes,
    ) -> AppResult<StatusCode> {
        let key = self.resolve_key_expression(selector, AdminOperation::Write)?;
        let encoding =
            request_encoding(headers, ::zenoh::bytes::Encoding::APPLICATION_OCTET_STREAM);

        self.state.zenoh().put_bytes(key, body, encoding).await?;
        Ok(StatusCode::OK)
    }

    pub(crate) async fn delete(&self, selector: &str) -> AppResult<StatusCode> {
        let key = self.resolve_key_expression(selector, AdminOperation::Write)?;
        self.state.zenoh().delete(key).await?;
        Ok(StatusCode::OK)
    }

    pub(crate) fn empty_selector_response<T>(&self) -> AppResult<T> {
        Err(AppError::bad_request(EMPTY_SELECTOR_MESSAGE))
    }

    fn resolve_key_expression(
        &self,
        selector: &str,
        operation: AdminOperation,
    ) -> AppResult<KeyExpression> {
        KeyResolver::new(&self.state).resolve(selector, operation)
    }

    fn build_query_request(
        &self,
        key: KeyExpression,
        query: Option<String>,
        headers: &HeaderMap,
        body: Bytes,
        timeout: Duration,
    ) -> ResolvedQuery {
        let raw_query = query.unwrap_or_default();
        let parameters = ::zenoh::query::Parameters::from(raw_query.clone());
        let raw = query_contains_parameter(&raw_query, RAW_KEY);
        let consolidation = if query_contains_parameter(&raw_query, "_time") {
            ::zenoh::query::QueryConsolidation::from(::zenoh::query::ConsolidationMode::None)
        } else {
            ::zenoh::query::QueryConsolidation::from(::zenoh::query::ConsolidationMode::Latest)
        };
        let selector = ::zenoh::query::Selector::owned(key, parameters);
        let mut request = ZenohQueryRequest::new(selector, timeout, consolidation);

        if !body.is_empty() {
            request = request
                .with_payload(body, request_encoding(headers, ::zenoh::bytes::Encoding::default()));
        }

        ResolvedQuery { request, raw }
    }

    fn ensure_empty_body(&self, body: &Bytes, message: &'static str) -> AppResult<()> {
        if body.is_empty() { Ok(()) } else { Err(AppError::bad_request(message)) }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct ZenohRestIndexResponse {
    service: &'static str,
    enabled: bool,
    admin_space: ZenohRestAdminSpaceResponse,
    state: crate::zenoh::ZenohStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct ZenohRestAdminSpaceResponse {
    route_enabled: bool,
    router_enabled: bool,
    read: bool,
    write: bool,
}

#[derive(Debug, Clone)]
struct ResolvedQuery {
    request: ZenohQueryRequest,
    raw: bool,
}

#[derive(Debug, Clone)]
struct QueryOptions {
    query: Option<String>,
    raw: bool,
    mode: QueryMode,
}

impl QueryOptions {
    fn parse(query: Option<String>) -> AppResult<Self> {
        let raw_query = query.clone().unwrap_or_default();
        let raw = query_contains_parameter(&raw_query, RAW_KEY);

        if !query_contains_parameter(&raw_query, LIVELINESS_KEY) {
            return Ok(Self { query, raw, mode: QueryMode::Standard });
        }

        let unsupported = query_parameter_names(&raw_query)
            .filter_map(|(name, _)| match name {
                RAW_KEY | LIVELINESS_KEY | HISTORY_KEY => None,
                _ => Some(name.to_owned()),
            })
            .collect::<Vec<_>>();

        if !unsupported.is_empty() {
            return Err(AppError::bad_request(format!(
                "Zenoh liveliness requests do not accept selector parameters other than `_liveliness`, `_history`, and `_raw`: {}.",
                unsupported.join(", ")
            )));
        }

        Ok(Self {
            query: None,
            raw,
            mode: QueryMode::Liveliness {
                history: query_contains_parameter(&raw_query, HISTORY_KEY),
            },
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum QueryMode {
    Standard,
    Liveliness { history: bool },
}

fn request_encoding(
    headers: &HeaderMap,
    default: ::zenoh::bytes::Encoding,
) -> ::zenoh::bytes::Encoding {
    headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(::zenoh::bytes::Encoding::from)
        .unwrap_or(default)
}

fn query_contains_parameter(query: &str, key: &str) -> bool {
    query_parameter_names(query).any(|(name, _)| name == key)
}

fn query_parameter_names(query: &str) -> impl Iterator<Item = (&str, &str)> {
    query
        .split(['&', ';'])
        .filter(|segment| !segment.is_empty())
        .map(|segment| segment.split_once('=').unwrap_or((segment, "")))
}

fn render_replies(response_kind: ResponseKind, replies: Vec<::zenoh::query::Reply>) -> Response {
    match response_kind {
        ResponseKind::EventStream => unreachable!("event-stream handled before querying"),
        ResponseKind::Html => html_response(replies),
        ResponseKind::Json => json_response(
            replies.iter().map(reply_to_json_sample).collect::<Vec<ZenohJsonSample>>(),
        ),
    }
}

fn index_response(index: ZenohRestIndexResponse) -> Response {
    Json(index).into_response()
}
