use std::borrow::Cow;
use std::cmp::Reverse;
use std::convert::Infallible;
use std::num::NonZeroU16;

use axum::Json;
use axum::body::Bytes;
use axum::extract::FromRequestParts;
use axum::http::header::{self, CONTENT_ENCODING, CONTENT_TYPE};
use axum::http::request::Parts;
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use html_escape::encode_text_to_string;
use mime::Mime;
use zenoh::bytes::{Encoding, ZBytes};
use zenoh::query::Reply;

use super::metadata::{decode_content_type_parameters, normalize_content_codings};
use crate::zenoh::ZenohJsonSample;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ApiBodyFormat {
    Html,
    Json,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NegotiatedResponse {
    EventStream,
    Api(ApiBodyFormat),
}

impl NegotiatedResponse {
    const DEFAULT: Self = Self::Api(ApiBodyFormat::Json);

    fn from_headers(headers: &HeaderMap) -> Self {
        negotiate_response(headers)
    }
}

impl<S> FromRequestParts<S> for NegotiatedResponse
where
    S: Send + Sync,
{
    type Rejection = Infallible;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        Ok(Self::from_headers(&parts.headers))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ServerRepresentation {
    EventStream,
    Html,
    Json,
}

impl ServerRepresentation {
    const ALL: [Self; 3] = [Self::EventStream, Self::Html, Self::Json];

    fn negotiated_response(self) -> NegotiatedResponse {
        match self {
            Self::EventStream => NegotiatedResponse::EventStream,
            Self::Html => NegotiatedResponse::Api(ApiBodyFormat::Html),
            Self::Json => NegotiatedResponse::Api(ApiBodyFormat::Json),
        }
    }

    fn media_type(self) -> MediaType {
        match self {
            Self::EventStream => MediaType { type_: "text", subtype: "event-stream" },
            Self::Html => MediaType { type_: "text", subtype: "html" },
            Self::Json => MediaType { type_: "application", subtype: "json" },
        }
    }

    /// Server-side tie breaker for broad matches.
    ///
    /// This preserves the pleasant API defaults:
    /// - `*/*` picks JSON.
    /// - `application/*` picks JSON.
    /// - `text/*` picks HTML instead of SSE.
    /// - exact `text/event-stream` still picks SSE.
    fn server_preference(self) -> u8 {
        match self {
            Self::EventStream => 0,
            Self::Html => 1,
            Self::Json => 2,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct MediaType {
    type_: &'static str,
    subtype: &'static str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct PositiveQValue(NonZeroU16);

impl PositiveQValue {
    const MAX: u16 = 1000;

    fn new(value: u16) -> Option<Self> {
        if (1..=Self::MAX).contains(&value) { NonZeroU16::new(value).map(Self) } else { None }
    }

    fn full() -> Self {
        Self(NonZeroU16::new(Self::MAX).expect("1000 is non-zero"))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum QValue {
    Zero,
    Positive(PositiveQValue),
}

impl QValue {
    fn full() -> Self {
        Self::Positive(PositiveQValue::full())
    }

    fn positive(self) -> Option<PositiveQValue> {
        match self {
            Self::Zero => None,
            Self::Positive(value) => Some(value),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum MediaRangeSpecificity {
    Any,
    TypeWildcard,
    Exact,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct AcceptMediaRange<'a> {
    type_: &'a str,
    subtype: &'a str,
    qvalue: QValue,
    order: usize,
}

impl<'a> AcceptMediaRange<'a> {
    fn parse(value: &'a str, order: usize) -> Option<Self> {
        let mut segments = value.split(';').map(str::trim);

        let media_range = segments.next()?;
        let (type_, subtype) = media_range.split_once('/')?;

        let mut qvalue = QValue::full();

        for parameter in segments {
            let Some((name, value)) = parameter.split_once('=') else {
                continue;
            };

            if name.trim().eq_ignore_ascii_case("q") {
                qvalue = parse_qvalue(value.trim())?;
            }
        }

        Some(Self { type_: type_.trim(), subtype: subtype.trim(), qvalue, order })
    }

    fn matches(self, representation: ServerRepresentation) -> Option<AcceptMatch> {
        let media_type = representation.media_type();

        let exact = self
            .type_
            .eq_ignore_ascii_case(media_type.type_)
            && self
                .subtype
                .eq_ignore_ascii_case(media_type.subtype);

        // SSE is streaming and should be selected only when explicitly requested.
        // Broad ranges like `text/*` or `*/*` must not accidentally opt clients
        // into an event-stream response.
        if representation == ServerRepresentation::EventStream && !exact {
            return None;
        }

        let specificity = if self.type_ == "*" && self.subtype == "*" {
            MediaRangeSpecificity::Any
        } else if self
            .type_
            .eq_ignore_ascii_case(media_type.type_)
            && self.subtype == "*"
        {
            MediaRangeSpecificity::TypeWildcard
        } else if exact {
            MediaRangeSpecificity::Exact
        } else {
            return None;
        };

        Some(AcceptMatch { qvalue: self.qvalue, specificity, accept_order: self.order })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct AcceptMatch {
    qvalue: QValue,
    specificity: MediaRangeSpecificity,
    accept_order: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct NegotiationScore {
    qvalue: PositiveQValue,
    specificity: MediaRangeSpecificity,
    accept_order: Reverse<usize>,
    server_preference: u8,
}

fn negotiate_response(headers: &HeaderMap) -> NegotiatedResponse {
    let accept_ranges = headers
        .get_all(header::ACCEPT)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .enumerate()
        .filter_map(|(order, value)| AcceptMediaRange::parse(value, order))
        .collect::<Vec<_>>();

    if accept_ranges.is_empty() {
        return NegotiatedResponse::DEFAULT;
    }

    ServerRepresentation::ALL
        .into_iter()
        .filter_map(|representation| {
            score_representation(representation, &accept_ranges)
                .map(|score| (score, representation))
        })
        .max_by_key(|(score, _)| *score)
        .map_or(NegotiatedResponse::DEFAULT, |(_, representation)| {
            representation.negotiated_response()
        })
}

fn score_representation(
    representation: ServerRepresentation,
    accept_ranges: &[AcceptMediaRange<'_>],
) -> Option<NegotiationScore> {
    let best_match = accept_ranges
        .iter()
        .filter_map(|range| range.matches(representation))
        .max_by_key(|matched| {
            (matched.specificity, matched.qvalue, Reverse(matched.accept_order))
        })?;

    Some(NegotiationScore {
        qvalue: best_match.qvalue.positive()?,
        specificity: best_match.specificity,
        accept_order: Reverse(best_match.accept_order),
        server_preference: representation.server_preference(),
    })
}

fn parse_qvalue(value: &str) -> Option<QValue> {
    let raw = match value.trim().as_bytes() {
        b"0" => 0,
        b"1" => 1000,
        [b'0', b'.', decimals @ ..] => parse_zero_prefixed_qvalue(decimals)?,
        [b'1', b'.', decimals @ ..]
            if decimals.len() <= 3
                && decimals
                    .iter()
                    .all(|byte| *byte == b'0') =>
        {
            1000
        },
        _ => return None,
    };

    if raw == 0 { Some(QValue::Zero) } else { PositiveQValue::new(raw).map(QValue::Positive) }
}

fn parse_zero_prefixed_qvalue(decimals: &[u8]) -> Option<u16> {
    if decimals.len() > 3 || !decimals.iter().all(u8::is_ascii_digit) {
        return None;
    }

    let mut value = 0_u16;

    for &byte in decimals {
        value = value * 10 + u16::from(byte - b'0');
    }

    for _ in decimals.len()..3 {
        value *= 10;
    }

    Some(value)
}

pub(crate) fn json_api_response(samples: Vec<ZenohJsonSample>) -> Response {
    Json(samples).into_response()
}

pub(crate) fn html_api_response<'a>(replies: impl IntoIterator<Item = &'a Reply>) -> Response {
    let mut body = String::from("<dl>\n");

    for reply in replies {
        write_html_reply(reply, &mut body);
    }

    body.push_str("</dl>\n");

    Html(body).into_response()
}

pub(crate) fn raw_zenoh_response(reply: Option<Reply>) -> Response {
    reply
        .map(HttpReply::from)
        .map_or_else(|| StatusCode::OK.into_response(), IntoResponse::into_response)
}

fn write_html_reply(reply: &Reply, output: &mut String) {
    match reply.result() {
        Ok(sample) => {
            write_html_definition(sample.key_expr().as_str(), sample.payload(), output);
        },
        Err(error) => {
            write_html_definition("ERROR", error.payload(), output);
        },
    }
}

fn write_html_definition(label: &str, payload: &ZBytes, output: &mut String) {
    let payload = display_payload(payload);

    output.push_str("<dt>");
    encode_text_to_string(label, output);
    output.push_str("</dt>\n<dd>");
    encode_text_to_string(payload.as_ref(), output);
    output.push_str("</dd>\n");
}

fn display_payload(payload: &ZBytes) -> Cow<'_, str> {
    payload
        .try_to_string()
        .unwrap_or_else(|_| format!("[binary payload: {} bytes]", payload.len()).into())
}

#[derive(Debug, Clone)]
struct HttpReply {
    body: Bytes,
    headers: HttpReplyHeaders,
}

impl From<Reply> for HttpReply {
    fn from(reply: Reply) -> Self {
        match reply.result() {
            Ok(sample) => {
                Self::from_payload(sample.encoding(), sample.attachment(), sample.payload())
            },
            Err(error) => Self::from_payload(error.encoding(), None, error.payload()),
        }
    }
}

impl IntoResponse for HttpReply {
    fn into_response(self) -> Response {
        let mut response = (StatusCode::OK, self.body).into_response();
        self.headers
            .write_to(response.headers_mut());
        response
    }
}

impl HttpReply {
    fn from_payload(encoding: &Encoding, attachment: Option<&ZBytes>, payload: &ZBytes) -> Self {
        Self::from_parts(encoding, attachment, payload.to_bytes().into_owned().into())
    }

    fn from_parts(encoding: &Encoding, attachment: Option<&ZBytes>, body: Bytes) -> Self {
        Self { body, headers: HttpReplyHeaders::from_zenoh_metadata(encoding, attachment) }
    }
}

#[derive(Debug, Clone, Default)]
struct HttpReplyHeaders {
    content_type: Option<HeaderValue>,
    content_encoding: Option<HeaderValue>,
}

impl HttpReplyHeaders {
    fn from_zenoh_metadata(encoding: &Encoding, attachment: Option<&ZBytes>) -> Self {
        let Some(encoding) = ZenohEncoding::parse(encoding) else {
            return Self::default();
        };

        let content_type_parameters =
            decode_content_type_parameters(attachment).unwrap_or_default();

        Self {
            content_type: encoding.content_type_header(&content_type_parameters),
            content_encoding: encoding.content_encoding,
        }
    }

    fn write_to(self, headers: &mut HeaderMap) {
        if let Some(content_type) = self.content_type {
            headers.insert(CONTENT_TYPE, content_type);
        }

        if let Some(content_encoding) = self.content_encoding {
            headers.insert(CONTENT_ENCODING, content_encoding);
        }
    }
}

#[derive(Debug, Clone)]
struct ZenohEncoding {
    media_type: Mime,
    content_encoding: Option<HeaderValue>,
}

impl ZenohEncoding {
    fn parse(encoding: &Encoding) -> Option<Self> {
        let encoding = encoding.to_string();

        let (media_type, schema) = match encoding.split_once(';') {
            Some((media_type, schema)) => (media_type.trim(), Some(schema.trim())),
            None => (encoding.trim(), None),
        };

        Some(Self {
            media_type: parse_media_type(media_type)?,
            content_encoding: schema
                .and_then(|schema| parse_zenoh_content_encoding_schema(&encoding, schema)),
        })
    }

    fn content_type_header(&self, parameters: &[String]) -> Option<HeaderValue> {
        let base = format!("{}/{}", self.media_type.type_(), self.media_type.subtype());
        let value = join_content_type_parameters(&base, parameters);

        value
            .parse::<Mime>()
            .map_err(|error| {
                tracing::debug!(
                    error = %error,
                    value,
                    "ignoring invalid HTTP content type reconstructed from Zenoh sample"
                );
            })
            .ok()
            .and_then(|mime| parse_header_value(mime.as_ref(), "content type"))
    }
}

fn join_content_type_parameters(media_type: &str, parameters: &[String]) -> String {
    if parameters.is_empty() {
        media_type.to_owned()
    } else {
        format!("{media_type}; {}", parameters.join("; "))
    }
}

fn parse_media_type(value: &str) -> Option<Mime> {
    if value.is_empty() {
        return None;
    }

    value
        .parse::<Mime>()
        .map_err(|error| {
            tracing::debug!(
                error = %error,
                value,
                "ignoring invalid Zenoh media type while reconstructing HTTP headers"
            );
        })
        .ok()
}

fn parse_zenoh_content_encoding_schema(full_encoding: &str, schema: &str) -> Option<HeaderValue> {
    let schema = schema.trim();

    if schema.is_empty() {
        return None;
    }

    if schema.contains(';') {
        tracing::debug!(
            full_encoding,
            schema,
            "ignoring unsupported legacy Zenoh schema while reconstructing HTTP headers"
        );
        return None;
    }

    let Some(codings) = normalize_content_codings(schema) else {
        tracing::debug!(
            full_encoding,
            schema,
            "ignoring invalid Zenoh content-encoding schema while reconstructing HTTP headers"
        );
        return None;
    };

    parse_header_value(&codings, "content encoding")
}

fn parse_header_value(value: &str, label: &'static str) -> Option<HeaderValue> {
    HeaderValue::from_str(value).map_or_else(
        |error| {
            tracing::debug!(
                error = %error,
                label,
                value,
                "ignoring invalid Zenoh HTTP header value"
            );
            None
        },
        Some,
    )
}

#[cfg(test)]
mod tests {
    use axum::http::{HeaderMap, header};
    use bytes::Bytes;

    use super::{
        ApiBodyFormat, Encoding, HttpReply, NegotiatedResponse, PositiveQValue, QValue, ZBytes,
        negotiate_response, parse_qvalue,
    };
    use crate::zenoh::rest::metadata::encode_content_type_parameters;

    fn q(value: u16) -> QValue {
        if value == 0 {
            QValue::Zero
        } else {
            QValue::Positive(PositiveQValue::new(value).expect("test qvalue should be valid"))
        }
    }

    #[test]
    fn accept_header_defaults_to_json_when_absent() {
        let headers = HeaderMap::new();

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn accept_header_prefers_highest_quality_response() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html;q=0.1, application/json;q=0.9"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn accept_header_prefers_first_exact_match_when_quality_is_equal() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html, application/json"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Html));
    }

    #[test]
    fn accept_header_matches_media_types_case_insensitively() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "Application/JSON"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn accept_header_ignores_zero_quality_media_ranges() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html;q=0, application/json;q=0.5"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn accept_header_specific_zero_quality_overrides_wildcard() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html;q=0, text/*;q=1, */*;q=0.5"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn accept_header_prefers_html_for_text_wildcard() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/*"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Html));
    }

    #[test]
    fn accept_header_prefers_json_for_any_wildcard() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "*/*"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn accept_header_prefers_event_stream_when_explicitly_requested() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/event-stream"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::EventStream);
    }

    #[test]
    fn accept_header_reads_repeated_accept_headers() {
        let mut headers = HeaderMap::new();
        headers.append(
            header::ACCEPT,
            "text/html;q=0.2"
                .parse()
                .expect("header should parse"),
        );
        headers.append(
            header::ACCEPT,
            "application/json;q=0.8"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn parse_qvalue_supports_thousandths() {
        assert_eq!(parse_qvalue("0.125"), Some(q(125)));
        assert_eq!(parse_qvalue("1"), Some(q(1000)));
        assert_eq!(parse_qvalue("0"), Some(q(0)));
        assert_eq!(parse_qvalue("1.1"), None);
    }

    #[test]
    fn parse_qvalue_supports_empty_fractional_suffix_allowed_by_qvalue() {
        assert_eq!(parse_qvalue("0."), Some(q(0)));
        assert_eq!(parse_qvalue("1."), Some(q(1000)));
    }

    #[test]
    fn raw_reply_reads_content_encoding_from_zenoh_schema() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "gzip",
        );
    }

    #[test]
    fn raw_reply_supports_multiple_content_encoding_tokens() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;gzip, br"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "gzip, br",
        );
    }

    #[test]
    fn raw_reply_ignores_legacy_mime_parameters_in_encoding() {
        let raw = HttpReply::from_parts(
            &Encoding::from("text/html; charset=utf-8"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html",
        );
        assert!(raw.headers.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_ignores_legacy_multi_segment_encoding() {
        let raw = HttpReply::from_parts(
            &Encoding::from("text/html; charset=utf-8;gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html",
        );
        assert!(raw.headers.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_ignores_legacy_content_encoding_parameter() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;content-encoding=gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert!(raw.headers.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_passes_through_extension_content_encoding() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;x-custom-coding"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "x-custom-coding",
        );
    }

    #[test]
    fn raw_reply_rebuilds_content_type_parameters_from_attachment() {
        let attachment = encode_content_type_parameters(&["charset=utf-8"])
            .expect("attachment should serialize");

        let raw = HttpReply::from_parts(
            &Encoding::from("text/html;br"),
            Some(&ZBytes::from(attachment)),
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html; charset=utf-8",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "br",
        );
    }
}
