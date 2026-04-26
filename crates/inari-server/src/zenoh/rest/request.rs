use axum::extract::FromRequestParts;
use axum::http::header::{self};
use axum::http::request::Parts;
use bytes::Bytes;
use zenoh::bytes::Encoding;
use zenoh::query::{ConsolidationMode, QueryConsolidation};

use super::metadata::{
    encode_content_type_parameters, normalize_content_codings, split_content_type,
};
use crate::error::{AppError, AppResult};

const RAW_KEY: &str = "_raw";
const LIVELINESS_KEY: &str = "_liveliness";
const HISTORY_KEY: &str = "_history";
const TIME_KEY: &str = "_time";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RawResponseMode {
    JsonEnvelope,
    RawHttp,
}

impl RawResponseMode {
    pub(crate) fn is_raw(self) -> bool {
        matches!(self, Self::RawHttp)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LivelinessMode {
    Current,
    WithHistory,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ConsolidationStrategy {
    Latest,
    None,
}

impl From<ConsolidationStrategy> for QueryConsolidation {
    fn from(value: ConsolidationStrategy) -> Self {
        match value {
            ConsolidationStrategy::Latest => QueryConsolidation::from(ConsolidationMode::Latest),
            ConsolidationStrategy::None => QueryConsolidation::from(ConsolidationMode::None),
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct StandardQueryOptions {
    selector_query: Option<String>,
    raw_response: RawResponseMode,
    consolidation: ConsolidationStrategy,
}

impl StandardQueryOptions {
    pub(crate) fn selector_query(&self) -> Option<&str> {
        self.selector_query.as_deref()
    }

    pub(crate) fn raw_response(&self) -> RawResponseMode {
        self.raw_response
    }

    pub(crate) fn consolidation(&self) -> ConsolidationStrategy {
        self.consolidation
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct LivelinessQueryOptions {
    raw_response: RawResponseMode,
    mode: LivelinessMode,
}

impl LivelinessQueryOptions {
    pub(crate) fn raw_response(self) -> RawResponseMode {
        self.raw_response
    }

    pub(crate) fn mode(self) -> LivelinessMode {
        self.mode
    }
}

#[derive(Debug, Clone)]
pub(crate) enum QueryOptions {
    Standard(StandardQueryOptions),
    Liveliness(LivelinessQueryOptions),
}

impl QueryOptions {
    fn parse(query: Option<&str>) -> AppResult<Self> {
        let query = query.unwrap_or_default();
        let query = (!query.is_empty()).then_some(query);
        let mut raw_response = RawResponseMode::JsonEnvelope;
        let mut liveliness = false;
        let mut history = false;
        let mut consolidation = ConsolidationStrategy::Latest;
        let mut saw_time = false;
        let mut unsupported = Vec::new();

        for (name, _value) in query_parameter_names(query.unwrap_or_default()) {
            match name {
                RAW_KEY => raw_response = RawResponseMode::RawHttp,
                LIVELINESS_KEY => liveliness = true,
                HISTORY_KEY => history = true,
                TIME_KEY => {
                    consolidation = ConsolidationStrategy::None;
                    saw_time = true;
                },
                _ => unsupported.push(name.to_owned()),
            }
        }

        if liveliness {
            if saw_time {
                unsupported.push(TIME_KEY.to_owned());
            }

            if !unsupported.is_empty() {
                return Err(AppError::bad_request(format!(
                    "Zenoh liveliness requests do not accept selector parameters other than `_liveliness`, `_history`, and `_raw`: {}.",
                    unsupported.join(", ")
                )));
            }

            return Ok(Self::Liveliness(LivelinessQueryOptions {
                raw_response,
                mode: if history { LivelinessMode::WithHistory } else { LivelinessMode::Current },
            }));
        }

        Ok(Self::Standard(StandardQueryOptions {
            selector_query: query.map(str::to_owned),
            raw_response,
            consolidation,
        }))
    }
}

impl<S> FromRequestParts<S> for QueryOptions
where
    S: Send + Sync,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        Self::parse(parts.uri.query())
    }
}

#[derive(Debug, Clone)]
pub(crate) struct RequestMetadata {
    media_type: Option<String>,
    content_encoding: Option<String>,
    attachment: Option<Bytes>,
}

impl RequestMetadata {
    pub(crate) fn into_transport(self, default: Encoding) -> RequestTransport {
        let media_type = self
            .media_type
            .unwrap_or_else(|| default.to_string());
        let encoding = match self.content_encoding {
            Some(content_encoding) => Encoding::from(format!("{media_type};{content_encoding}")),
            None => Encoding::from(media_type),
        };

        RequestTransport { encoding, attachment: self.attachment }
    }
}

impl<S> FromRequestParts<S> for RequestMetadata
where
    S: Send + Sync,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        let content_type = parts
            .headers
            .get(header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let content_encoding = parts
            .headers
            .get(header::CONTENT_ENCODING)
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .and_then(|value| {
                let normalized = normalize_content_codings(value);
                if normalized.is_none() {
                    tracing::debug!(
                        content_encoding = value,
                        "ignoring invalid HTTP Content-Encoding while building Zenoh transport metadata"
                    );
                }
                normalized
            });

        let (media_type, attachment) = match content_type {
            Some(content_type) => {
                let Some((media_type, parameters)) = split_content_type(content_type) else {
                    tracing::debug!(
                        content_type,
                        "ignoring invalid HTTP Content-Type while building Zenoh transport metadata"
                    );
                    return Ok(Self { media_type: None, content_encoding: None, attachment: None });
                };

                (Some(media_type), encode_content_type_parameters(&parameters))
            },
            None => (None, None),
        };

        Ok(Self { media_type, content_encoding, attachment })
    }
}

#[derive(Debug, Clone)]
pub(crate) struct RequestTransport {
    pub(crate) encoding: Encoding,
    pub(crate) attachment: Option<Bytes>,
}

fn query_parameter_names(query: &str) -> impl Iterator<Item = (&str, &str)> {
    query
        .split(['&', ';'])
        .filter(|segment| !segment.is_empty())
        .map(|segment| {
            segment
                .split_once('=')
                .unwrap_or((segment, ""))
        })
}

#[cfg(test)]
mod tests {
    use axum::extract::FromRequestParts;
    use axum::http::header;
    use zenoh::bytes::Encoding;

    use super::{
        ConsolidationStrategy, LivelinessMode, QueryOptions, RawResponseMode, RequestMetadata,
    };

    #[test]
    fn query_options_default_to_standard_json_responses() {
        let options = QueryOptions::parse(None).expect("query options should parse");

        match options {
            QueryOptions::Standard(standard) => {
                assert_eq!(standard.selector_query(), None);
                assert_eq!(standard.raw_response(), RawResponseMode::JsonEnvelope);
                assert_eq!(standard.consolidation(), ConsolidationStrategy::Latest);
            },
            QueryOptions::Liveliness(_) => panic!("expected standard query options"),
        }
    }

    #[test]
    fn query_options_parse_liveliness_history_mode() {
        let options = QueryOptions::parse(Some("_liveliness&_history&_raw"))
            .expect("query options should parse");

        match options {
            QueryOptions::Liveliness(liveliness) => {
                assert_eq!(liveliness.raw_response(), RawResponseMode::RawHttp);
                assert_eq!(liveliness.mode(), LivelinessMode::WithHistory);
            },
            QueryOptions::Standard(_) => panic!("expected liveliness query options"),
        }
    }

    #[test]
    fn query_options_reject_extra_liveliness_parameters() {
        let error = QueryOptions::parse(Some("_liveliness&foo=bar"))
            .expect_err("query options should reject unsupported liveliness parameters");

        assert_eq!(error.code(), "bad_request");
    }

    #[test]
    fn query_options_reject_time_parameter_for_liveliness() {
        let error = QueryOptions::parse(Some("_liveliness&_time"))
            .expect_err("liveliness queries should reject time consolidation overrides");

        assert_eq!(error.code(), "bad_request");
    }

    #[tokio::test]
    async fn request_metadata_uses_default_when_http_headers_are_missing() {
        let mut parts = axum::http::Request::builder()
            .uri("/")
            .body(())
            .expect("request should build")
            .into_parts()
            .0;
        let metadata = RequestMetadata::from_request_parts(&mut parts, &())
            .await
            .expect("request metadata should parse");
        let transport = metadata.into_transport(Encoding::APPLICATION_JSON);

        assert_eq!(transport.encoding, Encoding::APPLICATION_JSON);
        assert!(transport.attachment.is_none());
    }

    #[tokio::test]
    async fn request_metadata_moves_content_type_parameters_into_attachment() {
        let request = axum::http::Request::builder()
            .uri("/")
            .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
            .header(header::CONTENT_ENCODING, "br")
            .body(())
            .expect("request should build");
        let (mut parts, _) = request.into_parts();
        let metadata = RequestMetadata::from_request_parts(&mut parts, &())
            .await
            .expect("request metadata should parse");
        let transport = metadata.into_transport(Encoding::APPLICATION_OCTET_STREAM);

        assert_eq!(transport.encoding.to_string(), "text/html;br");
        assert_eq!(
            String::from_utf8(
                transport
                    .attachment
                    .expect("attachment should exist")
                    .to_vec()
            )
            .expect("attachment should be utf-8 json"),
            r#"{"content_type_parameters":["charset=utf-8"]}"#
        );
    }

    #[tokio::test]
    async fn request_metadata_ignores_invalid_content_type() {
        let request = axum::http::Request::builder()
            .uri("/")
            .header(header::CONTENT_TYPE, "not a mime")
            .body(())
            .expect("request should build");
        let (mut parts, _) = request.into_parts();
        let metadata = RequestMetadata::from_request_parts(&mut parts, &())
            .await
            .expect("request metadata should parse");
        let transport = metadata.into_transport(Encoding::APPLICATION_OCTET_STREAM);

        assert_eq!(transport.encoding, Encoding::APPLICATION_OCTET_STREAM);
        assert!(transport.attachment.is_none());
    }

    #[test]
    fn request_metadata_transport_keeps_valid_content_encoding() {
        let metadata = RequestMetadata {
            media_type: Some(String::from("application/json")),
            content_encoding: Some(String::from("gzip, br")),
            attachment: None,
        };
        let transport = metadata.into_transport(Encoding::APPLICATION_OCTET_STREAM);

        assert_eq!(transport.encoding.to_string(), "application/json;gzip, br");
    }
}
