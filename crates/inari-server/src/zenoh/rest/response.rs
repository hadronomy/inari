use axum::{
    Json,
    body::Bytes,
    http::{
        HeaderMap, HeaderValue, StatusCode,
        header::{self, CONTENT_ENCODING, CONTENT_TYPE},
    },
    response::{IntoResponse, Response},
};

use super::super::ZenohJsonSample;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ResponseKind {
    EventStream,
    Html,
    Json,
}

pub(crate) fn preferred_response_kind(headers: &HeaderMap) -> ResponseKind {
    let Some(value) = headers.get(header::ACCEPT).and_then(|value| value.to_str().ok()) else {
        return ResponseKind::Json;
    };

    let mut best = None::<AcceptedResponse>;

    for (order, media_range) in value.split(',').enumerate() {
        let Some(candidate) = AcceptedResponse::parse(media_range, order) else {
            continue;
        };

        if best.as_ref().is_none_or(|current| candidate.is_preferred_to(current)) {
            best = Some(candidate);
        }
    }

    best.map(|accepted| accepted.kind).unwrap_or(ResponseKind::Json)
}

pub(crate) fn json_response(samples: Vec<ZenohJsonSample>) -> Response {
    Json(samples).into_response()
}

pub(crate) fn html_response(replies: Vec<::zenoh::query::Reply>) -> Response {
    let mut body = String::from("<dl>\n");

    for reply in &replies {
        body.push_str(&html_entry(reply));
    }

    body.push_str("</dl>\n");

    (StatusCode::OK, [(CONTENT_TYPE, HeaderValue::from_static("text/html; charset=utf-8"))], body)
        .into_response()
}

pub(crate) fn raw_response(reply: Option<::zenoh::query::Reply>) -> Response {
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
    kind: ResponseKind,
    quality: u16,
    specificity: u8,
    order: usize,
}

impl AcceptedResponse {
    fn parse(media_range: &str, order: usize) -> Option<Self> {
        let mut segments = media_range.split(';').map(str::trim);
        let media_type = segments.next()?.to_ascii_lowercase();
        let mut quality = 1000;

        for parameter in segments {
            let (name, value) = parameter.split_once('=')?;
            if name.eq_ignore_ascii_case("q") {
                quality = parse_quality(value)?;
            }
        }

        let (kind, specificity) = match media_type.as_str() {
            "text/event-stream" => (ResponseKind::EventStream, 3),
            "text/html" => (ResponseKind::Html, 3),
            "application/json" => (ResponseKind::Json, 3),
            "application/*" => (ResponseKind::Json, 2),
            "text/*" => (ResponseKind::Html, 2),
            "*/*" => (ResponseKind::Json, 1),
            _ => return None,
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

    if value == "1" || value == "1.0" || value == "1.00" || value == "1.000" {
        return Some(1000);
    }
    if value == "0" || value == "0.0" || value == "0.00" || value == "0.000" {
        return Some(0);
    }

    let decimals = value.strip_prefix("0.")?;
    if decimals.is_empty()
        || decimals.len() > 3
        || !decimals.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }

    let mut padded = decimals.to_owned();
    while padded.len() < 3 {
        padded.push('0');
    }

    padded.parse().ok()
}

fn html_entry(reply: &::zenoh::query::Reply) -> String {
    match reply.result() {
        Ok(sample) => {
            let key = escape_html(sample.key_expr().as_str());
            let value = escape_html(&sample.payload().try_to_string().unwrap_or_default());
            format!("<dt>{key}</dt>\n<dd>{value}</dd>\n")
        }
        Err(error) => {
            let value = escape_html(&error.payload().try_to_string().unwrap_or_default());
            format!("<dt>ERROR</dt>\n<dd>{value}</dd>\n")
        }
    }
}

fn escape_html(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());

    for character in value.chars() {
        match character {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&#39;"),
            _ => escaped.push(character),
        }
    }

    escaped
}

fn raw_reply(reply: ::zenoh::query::Reply) -> RawReply {
    match reply.result() {
        Ok(sample) => {
            RawReply::from_parts(sample.encoding(), sample.payload().to_bytes().into_owned().into())
        }
        Err(error) => {
            RawReply::from_parts(error.encoding(), error.payload().to_bytes().into_owned().into())
        }
    }
}

impl RawReply {
    fn from_parts(encoding: &::zenoh::bytes::Encoding, body: Bytes) -> Self {
        let mut content_type = None;
        let mut content_encoding = None;

        for (index, segment) in encoding
            .to_string()
            .split(';')
            .map(str::trim)
            .filter(|segment| !segment.is_empty())
            .enumerate()
        {
            if index == 0 {
                content_type = parse_header_value(segment, "content type");
                continue;
            }

            let Some((name, value)) = segment.split_once('=') else {
                continue;
            };

            if name.eq_ignore_ascii_case("content-encoding") {
                content_encoding =
                    parse_header_value(value.trim_matches('"').trim(), "content encoding");
            }
        }

        Self { body, content_type, content_encoding }
    }
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

    use super::{RawReply, ResponseKind, parse_quality, preferred_response_kind};

    #[test]
    fn accept_header_prefers_highest_quality_response() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html;q=0.1, application/json;q=0.9".parse().expect("header should parse"),
        );

        assert_eq!(preferred_response_kind(&headers), ResponseKind::Json);
    }

    #[test]
    fn accept_header_prefers_first_match_when_quality_is_equal() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            "text/html, application/json".parse().expect("header should parse"),
        );

        assert_eq!(preferred_response_kind(&headers), ResponseKind::Html);
    }

    #[test]
    fn parse_quality_supports_thousandths() {
        assert_eq!(parse_quality("0.125"), Some(125));
        assert_eq!(parse_quality("1"), Some(1000));
        assert_eq!(parse_quality("0"), Some(0));
        assert_eq!(parse_quality("1.1"), None);
    }

    #[test]
    fn raw_reply_splits_content_encoding_from_zenoh_encoding() {
        let raw = RawReply::from_parts(
            &::zenoh::bytes::Encoding::from("application/json;content-encoding=gzip"),
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.content_type.as_ref().expect("content type should exist"),
            "application/json",
        );
        assert_eq!(raw.content_encoding.as_ref().expect("content encoding should exist"), "gzip",);
    }
}
