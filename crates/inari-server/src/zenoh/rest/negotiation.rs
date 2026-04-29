use std::cmp::Reverse;
use std::convert::Infallible;
use std::num::NonZeroU16;

use axum::extract::FromRequestParts;
use axum::http::HeaderMap;
use axum::http::header;
use axum::http::request::Parts;

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

#[cfg(test)]
mod tests {
    use axum::http::{HeaderMap, header};

    use super::{
        ApiBodyFormat, NegotiatedResponse, PositiveQValue, QValue, negotiate_response, parse_qvalue,
    };

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
            "text/html;q=0.5"
                .parse()
                .expect("header should parse"),
        );
        headers.append(
            header::ACCEPT,
            "application/json;q=0.9"
                .parse()
                .expect("header should parse"),
        );

        assert_eq!(negotiate_response(&headers), NegotiatedResponse::Api(ApiBodyFormat::Json));
    }

    #[test]
    fn parse_qvalue_supports_thousandths() {
        assert_eq!(parse_qvalue("0"), Some(q(0)));
        assert_eq!(parse_qvalue("0.1"), Some(q(100)));
        assert_eq!(parse_qvalue("0.12"), Some(q(120)));
        assert_eq!(parse_qvalue("0.123"), Some(q(123)));
        assert_eq!(parse_qvalue("1"), Some(q(1000)));
        assert_eq!(parse_qvalue("1.0"), Some(q(1000)));
        assert_eq!(parse_qvalue("1.000"), Some(q(1000)));
        assert_eq!(parse_qvalue("0.000"), Some(q(0)));
    }

    #[test]
    fn parse_qvalue_supports_empty_fractional_suffix_allowed_by_qvalue() {
        assert_eq!(parse_qvalue("0."), Some(q(0)));
        assert_eq!(parse_qvalue("1."), Some(q(1000)));
    }
}
