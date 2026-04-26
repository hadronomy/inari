use std::borrow::Cow;

use axum::Json;
use axum::body::Bytes;
use axum::extract::FromRequestParts;
use axum::http::header::{self, CONTENT_ENCODING, CONTENT_TYPE};
use axum::http::request::Parts;
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use html_escape::encode_text_to_string;
use mime::Mime;
use zenoh::bytes::{Encoding, ZBytes};
use zenoh::query::Reply;

use super::metadata::{decode_content_type_parameters, normalize_content_codings};
use crate::zenoh::ZenohJsonSample;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ApiRenderKind {
    Html,
    Json,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AcceptPreference {
    EventStream,
    Api(ApiRenderKind),
}

impl AcceptPreference {
    fn from_headers(headers: &HeaderMap) -> Self {
        preferred_accept(headers)
    }
}

impl<S> FromRequestParts<S> for AcceptPreference
where
    S: Send + Sync,
{
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        Ok(Self::from_headers(&parts.headers))
    }
}

fn preferred_accept(headers: &HeaderMap) -> AcceptPreference {
    let accept = headers
        .get(header::ACCEPT)
        .and_then(|value| value.to_str().ok());

    let Some(value) = accept else {
        return AcceptPreference::Api(ApiRenderKind::Json);
    };

    let mut best = None::<AcceptedResponse>;

    for (order, media_range) in value.split(',').enumerate() {
        let Some(candidate) = AcceptedResponse::parse(media_range, order) else {
            continue;
        };

        if best
            .as_ref()
            .is_none_or(|current| candidate.is_preferred_to(current))
        {
            best = Some(candidate);
        }
    }

    best.map(|accepted| accepted.kind)
        .unwrap_or(AcceptPreference::Api(ApiRenderKind::Json))
}

pub(crate) fn json_response(samples: Vec<ZenohJsonSample>) -> Response {
    Json(samples).into_response()
}

pub(crate) fn html_response<'a>(replies: impl IntoIterator<Item = &'a Reply>) -> Response {
    let mut body = String::from("<dl>\n");

    for reply in replies {
        body.push_str(&html_entry(reply));
    }

    body.push_str("</dl>\n");

    (StatusCode::OK, [(CONTENT_TYPE, HeaderValue::from_static("text/html; charset=utf-8"))], body)
        .into_response()
}

pub(crate) fn raw_response(reply: Option<Reply>) -> Response {
    let Some(reply) = reply else {
        return StatusCode::OK.into_response();
    };

    let raw = raw_reply(reply);
    let mut headers = HeaderMap::new();

    if let Some(content_type) = raw.content_type {
        headers.insert(CONTENT_TYPE, content_type);
    }
    if let Some(content_encoding) = raw.content_encoding {
        headers.insert(CONTENT_ENCODING, content_encoding);
    }

    (StatusCode::OK, headers, raw.body).into_response()
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct AcceptedResponse {
    kind: AcceptPreference,
    quality: u16,
    specificity: u8,
    order: usize,
}

impl AcceptedResponse {
    fn parse(media_range: &str, order: usize) -> Option<Self> {
        let mut segments = media_range.split(';').map(str::trim);
        let media_type = segments.next()?;
        let mut quality = 1000;

        for parameter in segments {
            let (name, value) = parameter.split_once('=')?;
            if name.eq_ignore_ascii_case("q") {
                quality = parse_quality(value)?;
            }
        }

        if quality == 0 {
            return None;
        }

        let (kind, specificity) = if media_type.eq_ignore_ascii_case("text/event-stream") {
            (AcceptPreference::EventStream, 3)
        } else if media_type.eq_ignore_ascii_case("text/html") {
            (AcceptPreference::Api(ApiRenderKind::Html), 3)
        } else if media_type.eq_ignore_ascii_case("application/json") {
            (AcceptPreference::Api(ApiRenderKind::Json), 3)
        } else if media_type.eq_ignore_ascii_case("application/*") {
            (AcceptPreference::Api(ApiRenderKind::Json), 2)
        } else if media_type.eq_ignore_ascii_case("text/*") {
            (AcceptPreference::Api(ApiRenderKind::Html), 2)
        } else if media_type == "*/*" {
            (AcceptPreference::Api(ApiRenderKind::Json), 1)
        } else {
            return None;
        };

        Some(Self { kind, quality, specificity, order })
    }

    fn is_preferred_to(&self, other: &Self) -> bool {
        (self.quality, self.specificity) > (other.quality, other.specificity)
            || ((self.quality, self.specificity) == (other.quality, other.specificity)
                && self.order < other.order)
    }
}

#[derive(Debug, Clone)]
struct RawReply {
    body: Bytes,
    content_type: Option<HeaderValue>,
    content_encoding: Option<HeaderValue>,
}

fn parse_quality(value: &str) -> Option<u16> {
    let value = value.trim();
    match value.as_bytes() {
        b"0" => Some(0),
        b"1" | b"1.0" | b"1.00" | b"1.000" => Some(1000),
        [b'0', b'.', decimals @ ..] => parse_fractional_quality(decimals),
        [b'1', b'.', decimals @ ..]
            if !decimals.is_empty()
                && decimals.len() <= 3
                && decimals
                    .iter()
                    .all(|byte| *byte == b'0') =>
        {
            Some(1000)
        },
        _ => None,
    }
}

fn parse_fractional_quality(decimals: &[u8]) -> Option<u16> {
    if decimals.is_empty()
        || decimals.len() > 3
        || !decimals
            .iter()
            .all(|byte| byte.is_ascii_digit())
    {
        return None;
    }

    let mut quality = 0_u16;
    for &byte in decimals {
        quality = quality * 10 + u16::from(byte - b'0');
    }
    for _ in decimals.len()..3 {
        quality *= 10;
    }

    Some(quality)
}

fn html_entry(reply: &Reply) -> String {
    match reply.result() {
        Ok(sample) => html_definition_entry(sample.key_expr().as_str(), sample.payload()),
        Err(error) => html_definition_entry("ERROR", error.payload()),
    }
}

fn html_definition_entry(label: &str, payload: &ZBytes) -> String {
    let payload = html_payload(payload);
    let mut entry = String::from("<dt>");
    encode_text_to_string(label, &mut entry);
    entry.push_str("</dt>\n<dd>");
    encode_text_to_string(payload.as_ref(), &mut entry);
    entry.push_str("</dd>\n");
    entry
}

fn html_payload(payload: &ZBytes) -> Cow<'_, str> {
    payload
        .try_to_string()
        .unwrap_or_else(|_| format!("[binary payload: {} bytes]", payload.len()).into())
}

fn raw_reply(reply: Reply) -> RawReply {
    match reply.result() {
        Ok(sample) => raw_reply_parts(sample.encoding(), sample.attachment(), sample.payload()),
        Err(error) => raw_reply_parts(error.encoding(), None, error.payload()),
    }
}

fn raw_reply_parts(encoding: &Encoding, attachment: Option<&ZBytes>, payload: &ZBytes) -> RawReply {
    RawReply::from_parts(encoding, attachment, payload.to_bytes().into_owned().into())
}

impl RawReply {
    fn from_parts(encoding: &Encoding, attachment: Option<&ZBytes>, body: Bytes) -> Self {
        let parts = ZenohContentType::parse(encoding);
        let content_type_parameters =
            decode_content_type_parameters(attachment).unwrap_or_default();
        let content_type = parts.as_ref().and_then(|content_type| {
            parse_content_type_header(&content_type.media_type, &content_type_parameters)
        });
        let content_encoding = parts
            .and_then(|content_type| content_type.content_encoding)
            .and_then(parse_content_encoding_header);

        Self { body, content_type, content_encoding }
    }
}

#[derive(Debug, Clone)]
struct ZenohContentType {
    media_type: Mime,
    content_encoding: Option<String>,
}

impl ZenohContentType {
    fn parse(encoding: &Encoding) -> Option<Self> {
        let encoding = encoding.to_string();
        let (media_type, schema) = match encoding.split_once(';') {
            Some((media_type, schema)) => (media_type.trim(), Some(schema.trim())),
            None => (encoding.trim(), None),
        };

        Some(Self {
            media_type: parse_media_type(media_type)?,
            content_encoding: schema
                .and_then(|schema| parse_content_encoding_schema(&encoding, schema)),
        })
    }
}

fn join_content_type(media_type: &str, parameters: &[String]) -> String {
    if parameters.is_empty() {
        media_type.to_owned()
    } else {
        format!("{media_type}; {}", parameters.join("; "))
    }
}

fn parse_content_type_header(media_type: &Mime, parameters: &[String]) -> Option<HeaderValue> {
    let base = format!("{}/{}", media_type.type_(), media_type.subtype());
    let value = join_content_type(&base, parameters);
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

fn parse_content_encoding_header(value: String) -> Option<HeaderValue> {
    parse_header_value(&value, "content encoding")
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

fn parse_content_encoding_schema(full_encoding: &str, schema: &str) -> Option<String> {
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

    if let Some(codings) = normalize_content_codings(schema) {
        return Some(codings);
    }

    tracing::debug!(
        full_encoding,
        schema,
        "ignoring invalid Zenoh content-encoding schema while reconstructing HTTP headers"
    );
    None
}

fn parse_header_value(value: &str, label: &'static str) -> Option<HeaderValue> {
    HeaderValue::from_str(value).map_or_else(
        |error| {
            tracing::debug!(error = %error, label, value, "ignoring invalid Zenoh HTTP header value");
            None
        },
        Some,
    )
}

#[cfg(test)]
mod tests {
    use axum::http::{HeaderMap, header};
    use bytes::Bytes;

    use crate::zenoh::rest::metadata::encode_content_type_parameters;

    use super::{
        AcceptPreference, ApiRenderKind, Encoding, RawReply, ZBytes, parse_quality,
        preferred_accept,
    };

    #[test]
    fn accept_header_prefers_highest_quality_response() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html;q=0.1, application/json;q=0.9"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(preferred_accept(&headers), AcceptPreference::Api(ApiRenderKind::Json));
    }

    #[test]
    fn accept_header_prefers_first_match_when_quality_is_equal() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html, application/json"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(preferred_accept(&headers), AcceptPreference::Api(ApiRenderKind::Html));
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

        assert_eq!(preferred_accept(&headers), AcceptPreference::Api(ApiRenderKind::Json));
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

        assert_eq!(preferred_accept(&headers), AcceptPreference::Api(ApiRenderKind::Json));
    }

    #[test]
    fn parse_quality_supports_thousandths() {
        assert_eq!(parse_quality("0.125"), Some(125));
        assert_eq!(parse_quality("1"), Some(1000));
        assert_eq!(parse_quality("0"), Some(0));
        assert_eq!(parse_quality("1.1"), None);
    }

    #[test]
    fn raw_reply_reads_content_encoding_from_zenoh_schema() {
        let raw = RawReply::from_parts(
            &Encoding::from("application/json;gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "gzip",
        );
    }

    #[test]
    fn raw_reply_supports_multiple_content_encoding_tokens() {
        let raw = RawReply::from_parts(
            &Encoding::from("application/json;gzip, br"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "gzip, br",
        );
    }

    #[test]
    fn raw_reply_ignores_legacy_mime_parameters_in_encoding() {
        let raw = RawReply::from_parts(
            &Encoding::from("text/html; charset=utf-8"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html",
        );
        assert!(raw.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_ignores_legacy_multi_segment_encoding() {
        let raw = RawReply::from_parts(
            &Encoding::from("text/html; charset=utf-8;gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html",
        );
        assert!(raw.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_ignores_legacy_content_encoding_parameter() {
        let raw = RawReply::from_parts(
            &Encoding::from("application/json;content-encoding=gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert!(raw.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_passes_through_extension_content_encoding() {
        let raw = RawReply::from_parts(
            &Encoding::from("application/json;x-custom-coding"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "x-custom-coding",
        );
    }

    #[test]
    fn raw_reply_rebuilds_content_type_parameters_from_attachment() {
        let attachment = encode_content_type_parameters(&["charset=utf-8"])
            .expect("attachment should serialize");
        let raw = RawReply::from_parts(
            &Encoding::from("text/html;br"),
            Some(&ZBytes::from(attachment)),
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html; charset=utf-8",
        );
        assert_eq!(
            raw.content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "br",
        );
    }
}
